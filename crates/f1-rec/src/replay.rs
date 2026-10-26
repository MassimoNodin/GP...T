use std::fmt;
use std::io;
use std::net::{SocketAddr, UdpSocket};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use f1_format::{Digest, RecordKind, RecordingReader, Records};
use f1_pipeline::Histogram;

use crate::util::{Result, format_duration_ns, interrupt_flag, recording_files};

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Speed {
    Factor(f64),
    Max,
}

impl FromStr for Speed {
    type Err = String;

    fn from_str(text: &str) -> std::result::Result<Self, String> {
        let text = text.trim();
        if text.eq_ignore_ascii_case("max") {
            return Ok(Speed::Max);
        }
        match text.trim_end_matches(['x', 'X']).parse::<f64>() {
            Ok(factor) if factor > 0.0 && factor.is_finite() => Ok(Speed::Factor(factor)),
            _ => Err(format!("invalid speed {text:?}: use a positive number such as 1 or 2.5, or \"max\"")),
        }
    }
}

impl fmt::Display for Speed {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Speed::Factor(factor) => write!(f, "{factor}x"),
            Speed::Max => f.write_str("max"),
        }
    }
}

/// Which part of a recording to replay.
#[derive(clap::Args, Debug, Clone, Copy, Default)]
pub struct Window {
    /// Skip this many seconds from the start of the recording (e.g. menus before the race).
    #[arg(long, default_value_t = 0.0)]
    pub skip_secs: f64,
    /// Replay at most this many seconds of recording time after the skip.
    #[arg(long)]
    pub limit_secs: Option<f64>,
}

impl Window {
    fn skip_ns(&self) -> u64 {
        (self.skip_secs.max(0.0) * 1e9) as u64
    }

    fn limit_ns(&self) -> Option<u64> {
        self.limit_secs.map(|secs| (secs.max(0.0) * 1e9) as u64)
    }
}

pub struct ReplayPlan {
    pub files: Vec<PathBuf>,
    pub speed: Speed,
    pub window: Window,
}

pub struct ReplayOutcome {
    pub sent: Digest,
    pub send_errors: u64,
    pub wall: Duration,
    /// Recording time covered by what was sent.
    pub source_span_ns: u64,
    /// How late each datagram left relative to its schedule.
    pub lateness: Histogram,
    pub interrupted: bool,
}

impl ReplayOutcome {
    /// Recording time divided by wall time.
    pub fn effective_speed(&self) -> f64 {
        let wall = self.wall.as_secs_f64();
        if wall > 0.0 { self.source_span_ns as f64 / 1e9 / wall } else { 0.0 }
    }
}

/// Streams datagram records to `target`, reproducing their recorded spacing
/// scaled by the speed. Blocks are read on a separate thread so disk reads
/// never delay sends.
pub fn replay(
    socket: &UdpSocket,
    target: SocketAddr,
    plan: &ReplayPlan,
    stop: &AtomicBool,
    mut progress: impl FnMut(&Digest, u64),
) -> Result<ReplayOutcome> {
    let (tx, rx) = mpsc::sync_channel::<std::result::Result<Vec<u8>, String>>(64);
    let files = plan.files.clone();
    let reader = thread::spawn(move || {
        for path in files {
            let mut reader = match RecordingReader::open(&path) {
                Ok(reader) => reader,
                Err(err) => {
                    let _ = tx.send(Err(format!("{}: {err}", path.display())));
                    return;
                }
            };
            loop {
                let mut body = Vec::new();
                match reader.next_block(&mut body) {
                    Ok(Some(_)) => {
                        if tx.send(Ok(body)).is_err() {
                            return;
                        }
                    }
                    Ok(None) => break,
                    Err(err) => {
                        let _ = tx.send(Err(format!("{}: {err}", path.display())));
                        return;
                    }
                }
            }
        }
    });

    let mut sent = Digest::default();
    let mut send_errors = 0u64;
    let mut lateness = Histogram::new();
    let (skip_ns, limit_ns) = (plan.window.skip_ns(), plan.window.limit_ns());
    let mut recording_start = None;
    let mut first_ts = None;
    let mut last_offset = 0u64;
    let mut interrupted = false;
    let mut last_progress = Instant::now();
    let start = Instant::now();
    'blocks: for body in rx.iter() {
        let body = body?;
        for record in Records::new(&body).filter(|record| record.kind == RecordKind::DATAGRAM) {
            let recording_start = *recording_start.get_or_insert(record.ts_ns);
            if record.ts_ns.saturating_sub(recording_start) < skip_ns {
                continue;
            }
            let first = *first_ts.get_or_insert(record.ts_ns);
            let offset = record.ts_ns.saturating_sub(first);
            if limit_ns.is_some_and(|limit| offset > limit) {
                break 'blocks;
            }
            if let Speed::Factor(factor) = plan.speed {
                let due = Duration::from_nanos((offset as f64 / factor) as u64);
                wait_until(start + due);
                lateness.record(start.elapsed().saturating_sub(due).as_nanos() as u64);
            }
            match socket.send_to(record.payload, target) {
                Ok(_) => sent.add(record.payload),
                Err(_) => send_errors += 1,
            }
            last_offset = offset;
            if stop.load(Ordering::Relaxed) {
                interrupted = true;
                break 'blocks;
            }
            if last_progress.elapsed() >= Duration::from_secs(5) {
                last_progress = Instant::now();
                progress(&sent, offset);
            }
        }
    }
    let wall = start.elapsed();
    drop(rx);
    let _ = reader.join();
    Ok(ReplayOutcome { sent, send_errors, wall, source_span_ns: last_offset, lateness, interrupted })
}

/// Sleeps most of the way, then spins, so sends land within microseconds
/// of their schedule without depending on the OS timer resolution.
fn wait_until(deadline: Instant) {
    loop {
        let now = Instant::now();
        if now >= deadline {
            return;
        }
        let remaining = deadline - now;
        if remaining > Duration::from_millis(3) {
            thread::sleep(remaining - Duration::from_millis(2));
        } else if remaining > Duration::from_micros(200) {
            thread::yield_now();
        } else {
            std::hint::spin_loop();
        }
    }
}

pub fn sender_socket(target: SocketAddr) -> io::Result<UdpSocket> {
    let socket = UdpSocket::bind(if target.is_ipv4() { "0.0.0.0:0" } else { "[::]:0" })?;
    socket2::SockRef::from(&socket).set_send_buffer_size(4 << 20)?;
    Ok(socket)
}

pub fn run_cli(recording: &Path, target: SocketAddr, speed: Speed, window: Window) -> Result<ExitCode> {
    let plan = ReplayPlan { files: recording_files(recording)?, speed, window };
    let socket = sender_socket(target)?;
    let stop = interrupt_flag();
    eprintln!("replaying {} to {target} at {speed}", recording.display());
    let outcome = replay(&socket, target, &plan, &stop, |sent, offset| {
        eprintln!("  {} sent, at {} of recording time", sent.count, format_duration_ns(offset));
    })?;
    eprintln!(
        "{} {} datagrams ({} send errors) covering {} in {} (effective {:.2}x); lateness p99 {:.3} ms, max {:.3} ms",
        if outcome.interrupted { "interrupted after" } else { "sent" },
        outcome.sent.count,
        outcome.send_errors,
        format_duration_ns(outcome.source_span_ns),
        format_duration_ns(outcome.wall.as_nanos() as u64),
        outcome.effective_speed(),
        outcome.lateness.summary().p99_ms,
        outcome.lateness.summary().max_ms,
    );
    Ok(if outcome.send_errors == 0 { ExitCode::SUCCESS } else { ExitCode::FAILURE })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_speeds() {
        assert_eq!("2".parse::<Speed>(), Ok(Speed::Factor(2.0)));
        assert_eq!("0.5x".parse::<Speed>(), Ok(Speed::Factor(0.5)));
        assert_eq!("MAX".parse::<Speed>(), Ok(Speed::Max));
        assert!("0".parse::<Speed>().is_err());
        assert!("fast".parse::<Speed>().is_err());
    }
}
