use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use clap::Args;
use f1_pipeline::{Pipeline, PipelineConfig, PipelineEvent, ReceiverConfig, SinkConfig, WriterConfig, utc_stamp};
use serde_json::json;

use crate::inspect::print_summary;
use crate::util::{Result, format_duration_ns, interrupt_flag};

#[derive(Args, Debug)]
pub struct RecordArgs {
    /// Output .f1r file (must not exist), or an existing directory to create a time-stamped file in.
    #[arg(long)]
    out: PathBuf,
    /// Address and port the game sends telemetry to.
    #[arg(long, default_value = "0.0.0.0:20777")]
    listen: SocketAddr,
    /// Also send every datagram on to this address (e.g. the ingest host) as soon as it arrives.
    #[arg(long)]
    forward: Option<SocketAddr>,
    /// Stop automatically after this many seconds.
    #[arg(long)]
    duration_secs: Option<u64>,
    /// Short description stored in the recording, e.g. "Jeddah race, 22 cars, rain".
    #[arg(long)]
    label: Option<String>,
    /// Make written data durable at this interval in milliseconds.
    #[arg(long, default_value_t = 1000)]
    fsync_ms: u64,
    /// Requested socket receive buffer in KiB.
    #[arg(long, default_value_t = 4096)]
    recv_buffer_kib: usize,
}

pub fn run(args: RecordArgs) -> Result<ExitCode> {
    let out = if args.out.is_dir() {
        args.out.join(format!("{}.{}", utc_stamp(f1_format::unix_now_ns()), f1_format::EXTENSION))
    } else {
        args.out.clone()
    };
    let config = PipelineConfig {
        listen: args.listen,
        recv_buffer_bytes: args.recv_buffer_kib * 1024,
        consumer_queue_batches: 1,
        receiver: ReceiverConfig { forward: args.forward, ..ReceiverConfig::default() },
        writer: WriterConfig {
            sink: SinkConfig::File { path: out.clone() },
            metadata: json!({
                "tool": "f1-rec record",
                "version": env!("CARGO_PKG_VERSION"),
                "listen": args.listen.to_string(),
                "forward": args.forward.map(|addr| addr.to_string()),
                "label": args.label,
                "host_os": std::env::consts::OS,
            }),
            fsync_interval: Duration::from_millis(args.fsync_ms),
            stats_interval: Duration::from_secs(5),
            test_control: false,
        },
    };
    let pipeline = Pipeline::start(
        config,
        Vec::new(),
        Box::new(|event| {
            if let PipelineEvent::Stats(stats) = event {
                eprintln!(
                    "  {:>7.1}s  {} received ({:.0}/s)  dropped: app {} kernel {}  forwarded {} (errors {})  write errors {}",
                    stats.uptime_s,
                    stats.received,
                    stats.received_per_s,
                    stats.app_dropped,
                    stats.kernel_dropped.map_or("n/a".to_string(), |drops| drops.to_string()),
                    stats.forwarded,
                    stats.forward_errors,
                    stats.write_errors,
                );
            }
        }),
    )?;
    eprintln!("recording on {} to {}", pipeline.local_addr(), out.display());
    if let Some(forward) = args.forward {
        eprintln!("forwarding every datagram to {forward}");
    }
    eprintln!("receive buffer granted: {} bytes; press Ctrl+C to stop", pipeline.recv_buffer_bytes());

    let stop = interrupt_flag();
    let started = Instant::now();
    let deadline = args.duration_secs.map(Duration::from_secs);
    while !stop.load(Ordering::Relaxed)
        && !pipeline.writer_finished()
        && deadline.is_none_or(|limit| started.elapsed() < limit)
    {
        std::thread::sleep(Duration::from_millis(100));
    }

    let outcome = pipeline.stop()?;
    let stats = &outcome.final_stats;
    eprintln!(
        "stopped after {}; {} datagrams received, app drops {}, kernel drops {}, write errors {}",
        format_duration_ns(started.elapsed().as_nanos() as u64),
        stats.received,
        stats.app_dropped,
        stats.kernel_dropped.map_or("unknown on this OS".to_string(), |drops| drops.to_string()),
        stats.write_errors,
    );
    if let Some(footer) = &outcome.last_footer {
        print_summary(Some(&out), &footer.summary);
    }
    let lossless = stats.app_dropped == 0 && stats.kernel_dropped.unwrap_or(0) == 0 && stats.write_errors == 0;
    Ok(if lossless { ExitCode::SUCCESS } else { ExitCode::FAILURE })
}
