use std::fs;
use std::io;
use std::net::{SocketAddr, UdpSocket};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use f1_format::{Digest, Footer, Record, RecordKind, RecordingWriter, Records};
use serde_json::{Value, json};

use crate::consumer::{ConsumerHub, Scope};
use crate::control::{ControlMessage, Verdict, decode_control_record};
use crate::histogram::LatencySet;
use crate::receiver::{Intake, Taken};
use crate::{Batch, Clock, KernelSample, KernelSampler, Metrics, PipelineEvent, StatsSnapshot, utc_stamp};

#[derive(Debug, Clone)]
pub enum SinkConfig {
    /// One recording file, which must not already exist (the recorder).
    File { path: PathBuf },
    /// Rotating segments under `root/live/<start stamp>/`; stress-test runs
    /// are kept apart under `root/tests/<run id>/`.
    Archive { root: PathBuf, segment_bytes: u64 },
}

#[derive(Debug, Clone)]
pub struct WriterConfig {
    pub sink: SinkConfig,
    /// Stored in every file header, with the segment's role added.
    pub metadata: Value,
    /// How often written data is made durable; zero syncs after every batch.
    pub fsync_interval: Duration,
    pub stats_interval: Duration,
    /// Act on stress-test control datagrams (see [`crate::control`]).
    pub test_control: bool,
}

pub struct WriterOutcome {
    pub final_stats: StatsSnapshot,
    /// Footer of the last file closed; for [`SinkConfig::File`], the recording.
    pub last_footer: Option<Footer>,
    pub files_closed: u64,
}

pub(crate) type EventCallback = Box<dyn FnMut(PipelineEvent<'_>) + Send>;

struct RunDir {
    run_id: String,
    info: Value,
    dir: PathBuf,
    seq: u32,
    closed_digest: Digest,
}

/// Owns the open file and decides where the next file goes.
pub(crate) struct Sink {
    config: SinkConfig,
    metadata: Value,
    origin_unix_ns: u64,
    current: Option<RecordingWriter>,
    live_dir: Option<PathBuf>,
    live_seq: u32,
    run: Option<RunDir>,
    file_used: bool,
    last_footer: Option<Footer>,
    files_closed: u64,
}

impl Sink {
    pub fn new(config: SinkConfig, metadata: Value, origin_unix_ns: u64) -> io::Result<Self> {
        let mut sink = Self {
            config,
            metadata,
            origin_unix_ns,
            current: None,
            live_dir: None,
            live_seq: 0,
            run: None,
            file_used: false,
            last_footer: None,
            files_closed: 0,
        };
        match &sink.config {
            SinkConfig::File { .. } => sink.open_next()?,
            SinkConfig::Archive { root, .. } => fs::create_dir_all(root)?,
        }
        Ok(sink)
    }

    fn is_single_file(&self) -> bool {
        matches!(self.config, SinkConfig::File { .. })
    }

    fn open_next(&mut self) -> io::Result<()> {
        let (path, role) = if let Some(run) = &mut self.run {
            run.seq += 1;
            let role = json!({"role": "test-run", "run_id": run.run_id, "run_info": run.info, "segment": run.seq});
            (run.dir.join(segment_name(run.seq)), role)
        } else {
            match &self.config {
                SinkConfig::File { path } => {
                    if self.file_used {
                        return Err(io::Error::other("the recording file was already closed"));
                    }
                    self.file_used = true;
                    (path.clone(), json!({"role": "recording"}))
                }
                SinkConfig::Archive { root, .. } => {
                    let dir = match &self.live_dir {
                        Some(dir) => dir.clone(),
                        None => {
                            let dir = unique_dir(&root.join("live").join(utc_stamp(self.origin_unix_ns)))?;
                            self.live_dir = Some(dir.clone());
                            dir
                        }
                    };
                    self.live_seq += 1;
                    (dir.join(segment_name(self.live_seq)), json!({"role": "live", "segment": self.live_seq}))
                }
            }
        };
        let mut metadata = self.metadata.clone();
        if let (Some(target), Value::Object(extra)) = (metadata.as_object_mut(), role) {
            target.extend(extra);
        }
        self.current = Some(RecordingWriter::create(&path, self.origin_unix_ns, &metadata)?);
        Ok(())
    }

    fn write_block(&mut self, body: &[u8]) -> io::Result<()> {
        if self.current.is_none() {
            self.open_next()?;
        }
        let writer = self.current.as_mut().expect("opened above");
        writer.write_block(body)?;
        if let SinkConfig::Archive { segment_bytes, .. } = self.config
            && writer.len() >= segment_bytes
        {
            self.close("rotate")?;
        }
        Ok(())
    }

    fn sync(&mut self) -> io::Result<()> {
        match &mut self.current {
            Some(writer) => writer.sync(),
            None => Ok(()),
        }
    }

    fn close(&mut self, reason: &str) -> io::Result<()> {
        if let Some(writer) = self.current.take() {
            let footer = writer.finish(json!({"closed_reason": reason}))?;
            if let Some(run) = &mut self.run {
                run.closed_digest.merge(footer.summary.digest);
            }
            self.last_footer = Some(footer);
            self.files_closed += 1;
        }
        Ok(())
    }

    /// Drops a file after a write failure; it stays readable up to its last good block.
    fn abandon(&mut self) {
        self.current = None;
    }

    fn current_path(&self) -> Option<String> {
        self.current.as_ref().map(|writer| writer.path().display().to_string())
    }

    fn enter_run(&mut self, run_id: &str, info: Value) -> io::Result<PathBuf> {
        let SinkConfig::Archive { root, .. } = &self.config else {
            return Err(io::Error::new(io::ErrorKind::Unsupported, "test runs need an archive sink"));
        };
        let dir = unique_dir(&root.join("tests").join(sanitize(run_id)))?;
        self.close("test-run-start")?;
        self.run = Some(RunDir {
            run_id: run_id.to_string(),
            info,
            dir: dir.clone(),
            seq: 0,
            closed_digest: Digest::default(),
        });
        Ok(dir)
    }

    fn run_digest(&self) -> Digest {
        let mut digest = self.run.as_ref().map(|run| run.closed_digest).unwrap_or_default();
        if let Some(writer) = &self.current {
            digest.merge(writer.indexer().digest());
        }
        digest
    }

    fn leave_run(&mut self) -> io::Result<()> {
        let result = self.close("test-run-end");
        self.run = None;
        self.current = None;
        result
    }
}

fn segment_name(seq: u32) -> String {
    format!("seg-{seq:06}.{}", f1_format::EXTENSION)
}

fn sanitize(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') { c } else { '_' })
        .take(96)
        .collect();
    if cleaned.is_empty() || cleaned.starts_with('.') { format!("run{cleaned}") } else { cleaned }
}

/// Creates `base`, or `base-2`, `base-3`, ... if it already exists.
fn unique_dir(base: &Path) -> io::Result<PathBuf> {
    if let Some(parent) = base.parent() {
        fs::create_dir_all(parent)?;
    }
    for attempt in 1..10_000 {
        let candidate =
            if attempt == 1 { base.to_path_buf() } else { PathBuf::from(format!("{}-{attempt}", base.display())) };
        match fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(err) if err.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(err) => return Err(err),
        }
    }
    Err(io::Error::other("could not find a free directory name"))
}

struct RunState {
    id: String,
    dir: PathBuf,
    started: Instant,
    app_dropped: u64,
    write_errors: u64,
    kernel_drops: Option<u64>,
    consumer_missed: u64,
}

pub(crate) struct Writer {
    pub config: WriterConfig,
    pub clock: Clock,
    pub metrics: Arc<Metrics>,
    pub hub: ConsumerHub,
    pub kernel: Option<KernelSampler>,
    pub reply: Option<UdpSocket>,
    pub sink: Sink,
    pub on_event: EventCallback,
    pub recv_buffer_bytes: usize,
    pub intake: Arc<Intake>,
}

struct State {
    written: LatencySet,
    durable: LatencySet,
    pending_durable: Vec<u64>,
    started: Instant,
    last_sync: Instant,
    last_stats: Instant,
    last_kernel: Instant,
    kernel_base: Option<u64>,
    kernel_last: Option<KernelSample>,
    kernel_peak_interval: u64,
    kernel_peak_run: u64,
    intake_peak_interval: u64,
    intake_peak_run: u64,
    received_at_last_stats: u64,
    run: Option<RunState>,
    last_verdict: Option<Verdict>,
}

impl Writer {
    pub fn run(mut self) -> io::Result<WriterOutcome> {
        let now = Instant::now();
        let mut state = State {
            written: LatencySet::default(),
            durable: LatencySet::default(),
            pending_durable: Vec::with_capacity(1 << 16),
            started: now,
            last_sync: now,
            last_stats: now,
            last_kernel: now,
            kernel_base: None,
            kernel_last: None,
            kernel_peak_interval: 0,
            kernel_peak_run: 0,
            intake_peak_interval: 0,
            intake_peak_run: 0,
            received_at_last_stats: 0,
            run: None,
            last_verdict: None,
        };
        self.sample_kernel(&mut state);
        state.kernel_base = state.kernel_last.map(|sample| sample.drops);

        let tick = Duration::from_millis(50)
            .min(self.config.stats_interval)
            .min(self.config.fsync_interval.max(Duration::from_millis(1)));
        let result = loop {
            match self.intake.take(&self.clock, tick) {
                Taken::Batch(batch) => {
                    let pending = batch.len_bytes() as u64;
                    state.intake_peak_interval = state.intake_peak_interval.max(pending);
                    state.intake_peak_run = state.intake_peak_run.max(pending);
                    if let Err(err) = self.handle(&mut state, batch) {
                        break Err(err);
                    }
                }
                Taken::Nothing => {}
                Taken::Closed => break Ok(()),
            }
            if let Err(err) = self.housekeeping(&mut state) {
                break Err(err);
            }
        };

        if state.run.is_some() {
            let _ = self.finish_run(&mut state, None);
        }
        let _ = self.sync(&mut state);
        let closed = self.sink.close("shutdown");
        self.hub.shutdown();
        let final_stats = self.stats(&mut state);
        (self.on_event)(PipelineEvent::Stats(&final_stats));
        result?;
        closed?;
        Ok(WriterOutcome {
            final_stats,
            last_footer: self.sink.last_footer.take(),
            files_closed: self.sink.files_closed,
        })
    }

    fn handle(&mut self, state: &mut State, batch: Batch) -> io::Result<()> {
        let mut start = 0;
        let mut split = false;
        let mut records = Records::new(&batch.body);
        loop {
            let position = records.position();
            let Some(record) = records.next() else { break };
            if record.kind == RecordKind::CONTROL && self.config.test_control {
                let end = records.position();
                // Everything before a control record must reach the archive and
                // the consumers before the control record can end a run.
                self.write_range(state, &batch.body[start..position])?;
                self.hub.publish(Arc::new(Batch::from_body(&batch.body[start..position])));
                self.handle_control(state, &record, &batch.body[position..end])?;
                start = end;
                split = true;
            }
        }
        self.write_range(state, &batch.body[start..])?;
        Metrics::add(&self.metrics.written_records, u64::from(batch.records));
        if self.config.fsync_interval.is_zero() {
            self.sync(state)?;
        }
        let rest = if split { Batch::from_body(&batch.body[start..]) } else { batch };
        self.hub.publish(Arc::new(rest));
        Ok(())
    }

    /// Writes encoded records as one block. Errors are counted and survived,
    /// except for a single-file recording, where they end the recording.
    fn write_range(&mut self, state: &mut State, body: &[u8]) -> io::Result<()> {
        if body.is_empty() {
            return Ok(());
        }
        match self.sink.write_block(body) {
            Ok(()) => {
                let now = self.clock.now_ns();
                for record in Records::new(body).filter(|record| record.kind == RecordKind::DATAGRAM) {
                    state.written.record(now.saturating_sub(record.ts_ns));
                    state.pending_durable.push(record.ts_ns);
                }
                Ok(())
            }
            Err(err) => {
                let lost = Records::new(body).filter(|record| record.kind == RecordKind::DATAGRAM).count() as u64;
                self.storage_failure(&err, lost);
                if self.sink.is_single_file() { Err(err) } else { Ok(()) }
            }
        }
    }

    fn storage_failure(&mut self, err: &io::Error, lost: u64) {
        let failures = self.metrics.write_errors.fetch_add(1, Ordering::Relaxed) + 1;
        Metrics::add(&self.metrics.lost_on_write, lost);
        if failures.is_power_of_two() {
            eprintln!("storage failure #{failures}: {err}; {lost} datagrams not stored");
        }
        self.sink.abandon();
    }

    fn sync(&mut self, state: &mut State) -> io::Result<()> {
        state.last_sync = Instant::now();
        if state.pending_durable.is_empty() {
            return Ok(());
        }
        match self.sink.sync() {
            Ok(()) => {
                let now = self.clock.now_ns();
                for ts in state.pending_durable.drain(..) {
                    state.durable.record(now.saturating_sub(ts));
                }
                Ok(())
            }
            Err(err) => {
                let lost = state.pending_durable.len() as u64;
                state.pending_durable.clear();
                self.storage_failure(&err, lost);
                if self.sink.is_single_file() { Err(err) } else { Ok(()) }
            }
        }
    }

    fn housekeeping(&mut self, state: &mut State) -> io::Result<()> {
        if !state.pending_durable.is_empty() && state.last_sync.elapsed() >= self.config.fsync_interval {
            self.sync(state)?;
        }
        if state.last_kernel.elapsed() >= Duration::from_millis(250) {
            self.sample_kernel(state);
        }
        if state.last_stats.elapsed() >= self.config.stats_interval {
            let snapshot = self.stats(state);
            (self.on_event)(PipelineEvent::Stats(&snapshot));
        }
        Ok(())
    }

    fn sample_kernel(&mut self, state: &mut State) {
        state.last_kernel = Instant::now();
        if let Some(sample) = self.kernel.as_ref().and_then(KernelSampler::sample) {
            state.kernel_peak_interval = state.kernel_peak_interval.max(sample.rx_queue_bytes);
            state.kernel_peak_run = state.kernel_peak_run.max(sample.rx_queue_bytes);
            state.kernel_last = Some(sample);
        }
    }

    fn stats(&mut self, state: &mut State) -> StatsSnapshot {
        let now = Instant::now();
        let interval = now.duration_since(state.last_stats).as_secs_f64();
        state.last_stats = now;
        let m = &self.metrics;
        let received = Metrics::get(&m.received);
        let per_s = if interval > 0.0 { (received - state.received_at_last_stats) as f64 / interval } else { 0.0 };
        state.received_at_last_stats = received;
        let kernel_peak = std::mem::take(&mut state.kernel_peak_interval);
        StatsSnapshot {
            uptime_s: round3(now.duration_since(state.started).as_secs_f64()),
            interval_s: round3(interval),
            received,
            received_per_s: round3(per_s),
            received_bytes: Metrics::get(&m.received_bytes),
            app_dropped: Metrics::get(&m.app_dropped),
            kernel_dropped: self.kernel_drops(state),
            kernel_rx_queue_peak_bytes: state.kernel_last.map(|_| kernel_peak),
            recv_buffer_bytes: self.recv_buffer_bytes,
            recv_errors: Metrics::get(&m.recv_errors),
            forwarded: Metrics::get(&m.forwarded),
            forward_errors: Metrics::get(&m.forward_errors),
            written_records: Metrics::get(&m.written_records),
            write_errors: Metrics::get(&m.write_errors),
            lost_on_write: Metrics::get(&m.lost_on_write),
            intake_peak_bytes: std::mem::take(&mut state.intake_peak_interval),
            written_latency: state.written.take_interval(),
            durable_latency: state.durable.take_interval(),
            total_written_latency: state.written.total.summary(),
            total_durable_latency: state.durable.total.summary(),
            segment: self.sink.current_path(),
            test_run: state.run.as_ref().map(|run| run.id.clone()),
            consumers: self.hub.reports(Scope::Interval),
        }
    }

    fn kernel_drops(&self, state: &State) -> Option<u64> {
        Some(state.kernel_last?.drops.saturating_sub(state.kernel_base?))
    }

    fn reply(&self, target: SocketAddr, message: &ControlMessage) {
        if let Some(socket) = &self.reply {
            let _ = socket.send_to(&message.encode(), target);
        }
    }

    fn handle_control(&mut self, state: &mut State, record: &Record<'_>, raw: &[u8]) -> io::Result<()> {
        let Some((source, datagram)) = decode_control_record(record.payload) else {
            return self.write_range(state, raw);
        };
        match ControlMessage::decode(datagram) {
            Some(ControlMessage::StartRun { run_id, info }) => {
                if let Some(run) = state.run.as_ref().filter(|run| run.id == run_id) {
                    let archive = run.dir.display().to_string();
                    self.write_range(state, raw)?;
                    self.reply(source, &ControlMessage::Started { run_id, archive });
                    return Ok(());
                }
                if state.run.is_some() {
                    self.finish_run(state, None)?;
                }
                let reply = match self.start_run(state, &run_id, info) {
                    Ok(dir) => ControlMessage::Started { run_id, archive: dir.display().to_string() },
                    Err(err) => ControlMessage::Rejected { run_id, reason: err.to_string() },
                };
                self.write_range(state, raw)?;
                self.reply(source, &reply);
            }
            Some(ControlMessage::EndRun { run_id, sent }) => {
                self.write_range(state, raw)?;
                let reply = if state.run.as_ref().is_some_and(|run| run.id == run_id) {
                    ControlMessage::Verdict(Box::new(self.finish_run(state, Some(sent))?))
                } else if let Some(verdict) = state.last_verdict.as_ref().filter(|v| v.run_id == run_id) {
                    ControlMessage::Verdict(Box::new(verdict.clone()))
                } else {
                    ControlMessage::Rejected { run_id, reason: "no active run with this id".into() }
                };
                self.reply(source, &reply);
            }
            _ => self.write_range(state, raw)?,
        }
        Ok(())
    }

    fn start_run(&mut self, state: &mut State, run_id: &str, info: Value) -> io::Result<PathBuf> {
        self.sync(state)?;
        let dir = self.sink.enter_run(run_id, info)?;
        state.written.run.reset();
        state.durable.run.reset();
        self.hub.reset_run();
        state.intake_peak_run = 0;
        self.sample_kernel(state);
        state.kernel_peak_run = state.kernel_last.map_or(0, |sample| sample.rx_queue_bytes);
        state.run = Some(RunState {
            id: run_id.to_string(),
            dir: dir.clone(),
            started: Instant::now(),
            app_dropped: Metrics::get(&self.metrics.app_dropped),
            write_errors: Metrics::get(&self.metrics.write_errors),
            kernel_drops: state.kernel_last.map(|sample| sample.drops),
            consumer_missed: self.hub.missed_batches(),
        });
        (self.on_event)(PipelineEvent::RunStarted { run_id, archive: &dir });
        Ok(dir)
    }

    fn finish_run(&mut self, state: &mut State, expected: Option<Digest>) -> io::Result<Verdict> {
        self.sync(state)?;
        let consumers_drained = self.hub.wait_idle(Duration::from_secs(10));
        self.sample_kernel(state);
        let received = self.sink.run_digest();
        if let Err(err) = self.sink.leave_run() {
            self.storage_failure(&err, 0);
        }
        let run = state.run.take().expect("finish_run needs an active run");
        let app_dropped = Metrics::get(&self.metrics.app_dropped) - run.app_dropped;
        let write_errors = Metrics::get(&self.metrics.write_errors) - run.write_errors;
        let kernel_dropped = match (run.kernel_drops, state.kernel_last) {
            (Some(before), Some(after)) => Some(after.drops.saturating_sub(before)),
            _ => None,
        };
        let consumer_missed_batches = self.hub.missed_batches() - run.consumer_missed;
        let digest_match = expected.map(|expected| expected == received);
        let verdict = Verdict {
            run_id: run.id,
            aborted: expected.is_none(),
            expected,
            received,
            missing: expected.map(|expected| expected.count as i64 - received.count as i64),
            digest_match,
            app_dropped,
            kernel_dropped,
            kernel_rx_queue_peak_bytes: state.kernel_last.map(|_| state.kernel_peak_run),
            write_errors,
            intake_peak_bytes: state.intake_peak_run,
            consumer_missed_batches,
            consumers_drained,
            written_latency: state.written.run.summary(),
            durable_latency: state.durable.run.summary(),
            consumers: self.hub.reports(Scope::Run),
            duration_s: round3(run.started.elapsed().as_secs_f64()),
            archive: run.dir.display().to_string(),
            archive_lossless: digest_match == Some(true)
                && app_dropped == 0
                && kernel_dropped.unwrap_or(0) == 0
                && write_errors == 0,
            consumers_kept_up: consumer_missed_batches == 0 && consumers_drained,
        };
        let json = serde_json::to_vec_pretty(&verdict)?;
        if let Err(err) = fs::write(run.dir.join("verdict.json"), json) {
            eprintln!("could not save the verdict for {}: {err}", verdict.run_id);
        }
        (self.on_event)(PipelineEvent::RunFinished(&verdict));
        state.last_verdict = Some(verdict.clone());
        Ok(verdict)
    }
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}
