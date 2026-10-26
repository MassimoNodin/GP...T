//! `f1-ingest`: receives F1 25 telemetry and archives every datagram.
//!
//! Output is one JSON object per line on stdout (start, periodic stats,
//! stress-test runs, stop), suitable for journald or piping into `jq`.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::Ordering;
use std::time::Duration;

use clap::Parser;
use f1_pipeline::{
    Consumer, Pipeline, PipelineConfig, PipelineEvent, ReceiverConfig, SinkConfig, SyntheticLoad, WriterConfig,
};
use serde_json::json;

#[derive(Parser, Debug)]
#[command(name = "f1-ingest", version, about = "Receive F1 25 UDP telemetry and archive every datagram")]
struct Args {
    /// Address and port to receive telemetry on.
    #[arg(long, default_value = "0.0.0.0:20777")]
    listen: SocketAddr,
    /// Archive root. Live data goes to <archive>/live/<start time>/, stress runs to <archive>/tests/<run id>/.
    #[arg(long)]
    archive: PathBuf,
    /// Start a new segment file after this many MiB.
    #[arg(long, default_value_t = 1024)]
    segment_mib: u64,
    /// Make written data durable (fsync) at this interval; 0 syncs after every batch.
    #[arg(long, default_value_t = 1000)]
    fsync_ms: u64,
    /// Hand a batch to the writer once its oldest datagram is this old.
    #[arg(long, default_value_t = 10)]
    batch_ms: u64,
    /// ...or once it holds this many KiB.
    #[arg(long, default_value_t = 256)]
    batch_kib: usize,
    /// Requested socket receive buffer in KiB (the OS may cap it; the granted size is logged).
    #[arg(long, default_value_t = 4096)]
    recv_buffer_kib: usize,
    /// Seconds between stats lines.
    #[arg(long, default_value_t = 5)]
    stats_secs: u64,
    /// Accept stress-test runs from `f1-rec stress` on the telemetry port.
    #[arg(long)]
    test_control: bool,
    /// Add a consumer that burns this many microseconds of CPU per packet, to
    /// model the cost of a planned feature.
    #[arg(long)]
    synthetic_consumer_us: Option<u64>,
}

fn emit(value: serde_json::Value) {
    println!("{value}");
}

fn main() -> ExitCode {
    let args = Args::parse();
    let config = PipelineConfig {
        listen: args.listen,
        recv_buffer_bytes: args.recv_buffer_kib * 1024,
        consumer_queue_batches: 256,
        receiver: ReceiverConfig {
            batch_max_bytes: args.batch_kib * 1024,
            batch_max_age: Duration::from_millis(args.batch_ms.max(1)),
            ..ReceiverConfig::default()
        },
        writer: WriterConfig {
            sink: SinkConfig::Archive { root: args.archive.clone(), segment_bytes: args.segment_mib.max(1) << 20 },
            metadata: json!({
                "tool": "f1-ingest",
                "version": env!("CARGO_PKG_VERSION"),
                "listen": args.listen.to_string(),
            }),
            fsync_interval: Duration::from_millis(args.fsync_ms),
            stats_interval: Duration::from_secs(args.stats_secs.max(1)),
            test_control: args.test_control,
        },
    };
    let consumers: Vec<Box<dyn Consumer>> = args
        .synthetic_consumer_us
        .map(|us| Box::new(SyntheticLoad::new(Duration::from_micros(us))) as Box<dyn Consumer>)
        .into_iter()
        .collect();

    let on_event = Box::new(|event: PipelineEvent<'_>| match event {
        PipelineEvent::Stats(stats) => emit(json!({"event": "stats", "stats": stats})),
        PipelineEvent::RunStarted { run_id, archive } => {
            emit(json!({"event": "test_run_started", "run_id": run_id, "archive": archive}))
        }
        PipelineEvent::RunFinished(verdict) => emit(json!({"event": "test_run_finished", "verdict": verdict})),
    });
    let pipeline = match Pipeline::start(config, consumers, on_event) {
        Ok(pipeline) => pipeline,
        Err(err) => {
            eprintln!("f1-ingest: could not start: {err}");
            return ExitCode::FAILURE;
        }
    };
    emit(json!({
        "event": "started",
        "listen": pipeline.local_addr().to_string(),
        "archive": args.archive,
        "recv_buffer_bytes": pipeline.recv_buffer_bytes(),
        "kernel_drops_measurable": pipeline.kernel_drops_measurable(),
        "test_control": args.test_control,
        "fsync_ms": args.fsync_ms,
    }));

    let shutdown = pipeline.shutdown_flag();
    let handler_flag = pipeline.shutdown_flag();
    if let Err(err) = ctrlc::set_handler(move || handler_flag.store(true, Ordering::Relaxed)) {
        eprintln!("f1-ingest: could not install the signal handler: {err}");
    }
    while !shutdown.load(Ordering::Relaxed) && !pipeline.writer_finished() {
        std::thread::sleep(Duration::from_millis(100));
    }

    match pipeline.stop() {
        Ok(outcome) => {
            emit(json!({"event": "stopped", "files_closed": outcome.files_closed}));
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("f1-ingest: stopped with an error: {err}");
            ExitCode::FAILURE
        }
    }
}
