use std::fs;
use std::net::{SocketAddr, UdpSocket};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use clap::Args;
use f1_format::read_footer;
use f1_pipeline::LatencySummary;
use f1_pipeline::control::{ControlMessage, Verdict};
use f1_pipeline::utc_stamp;
use serde::Serialize;
use serde_json::json;

use crate::replay::{ReplayPlan, Speed, Window, replay, sender_socket};
use crate::util::{Result, file_stem, format_duration_ns, interrupt_flag, recording_files};

#[derive(Args, Debug)]
pub struct StressArgs {
    /// A .f1r recording, or a directory of archive segments.
    recording: PathBuf,
    /// The ingest's telemetry address, e.g. 192.168.1.115:20777.
    #[arg(long)]
    target: SocketAddr,
    /// Speeds to run in order, e.g. "1,2,4,8,max". The sweep stops at the first run that does not pass.
    #[arg(long, value_delimiter = ',', default_value = "1")]
    speeds: Vec<Speed>,
    #[command(flatten)]
    window: Window,
    /// Fail a run if p99 receive-to-written latency exceeds this.
    #[arg(long, default_value_t = 50.0)]
    max_written_p99_ms: f64,
    /// Fail a run if p99 receive-to-durable (fsync) latency exceeds this.
    #[arg(long, default_value_t = 2000.0)]
    max_durable_p99_ms: f64,
    /// A run is inconclusive if the sender achieves less than this fraction of the requested speed.
    #[arg(long, default_value_t = 0.95)]
    min_pace: f64,
    /// Keep running faster speeds after a run fails.
    #[arg(long)]
    keep_going: bool,
    /// Seconds to wait for the ingest's verdict after the last datagram.
    #[arg(long, default_value_t = 60)]
    verdict_timeout_secs: u64,
    /// Directory for the JSON report.
    #[arg(long, default_value = "stress-results")]
    out: PathBuf,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Outcome {
    Pass,
    Fail,
    /// The sender could not reach the requested speed, so the run proves nothing about it.
    Inconclusive,
}

#[derive(Debug, Serialize)]
struct RunResult {
    run_id: String,
    speed: String,
    effective_speed: f64,
    sent: u64,
    send_errors: u64,
    sender_lateness: LatencySummary,
    wall_s: f64,
    interrupted: bool,
    verdict: Option<Verdict>,
    outcome: Outcome,
    reasons: Vec<String>,
}

pub fn run(args: StressArgs) -> Result<ExitCode> {
    let files = recording_files(&args.recording)?;
    let mut recorded = 0u64;
    let mut span = 0u64;
    for file in &files {
        if let (_, Some(footer)) = read_footer(file)? {
            recorded += footer.summary.datagrams;
            span += footer.summary.span_ns();
        }
    }
    let stamp = utc_stamp(f1_format::unix_now_ns());
    let stem = file_stem(&args.recording);
    eprintln!(
        "stress test: {} ({} datagrams over {}) -> {} at speeds {}",
        args.recording.display(),
        recorded,
        format_duration_ns(span),
        args.target,
        args.speeds.iter().map(Speed::to_string).collect::<Vec<_>>().join(", ")
    );
    let stop = interrupt_flag();
    let mut results = Vec::new();
    for speed in &args.speeds {
        let run_id = format!("{stamp}-{stem}-{}", speed.to_string().replace('.', "_"));
        let result = run_one(&args, &files, *speed, &run_id, &stop)?;
        print_result(&result);
        let stop_sweep = result.outcome != Outcome::Pass && !args.keep_going;
        results.push(result);
        if stop.load(Ordering::Relaxed) || stop_sweep {
            break;
        }
    }

    let best = results
        .iter()
        .filter(|run| run.outcome == Outcome::Pass)
        .max_by(|a, b| a.effective_speed.partial_cmp(&b.effective_speed).unwrap_or(std::cmp::Ordering::Equal));
    println!();
    print_table(&results);
    match best {
        Some(run) => println!(
            "\nHighest passing speed: {} (effective {:.2}x, {:.0} datagrams/s)",
            run.speed,
            run.effective_speed,
            run.sent as f64 / run.wall_s.max(1e-9)
        ),
        None => println!("\nNo run passed."),
    }

    fs::create_dir_all(&args.out)?;
    let report_path = args.out.join(format!("{stamp}-{stem}.json"));
    let report = json!({
        "recording": args.recording,
        "recording_datagrams": recorded,
        "recording_span_s": span as f64 / 1e9,
        "target": args.target.to_string(),
        "skip_secs": args.window.skip_secs,
        "limit_secs": args.window.limit_secs,
        "budgets": {
            "max_written_p99_ms": args.max_written_p99_ms,
            "max_durable_p99_ms": args.max_durable_p99_ms,
            "min_pace": args.min_pace,
        },
        "tool_version": env!("CARGO_PKG_VERSION"),
        "sender_os": std::env::consts::OS,
        "highest_passing_speed": best.map(|run| run.speed.clone()),
        "highest_passing_effective_speed": best.map(|run| run.effective_speed),
        "runs": results,
    });
    fs::write(&report_path, serde_json::to_vec_pretty(&report)?)?;
    println!("Report: {}", report_path.display());

    let first_passed = results.first().is_some_and(|run| run.outcome == Outcome::Pass);
    Ok(if first_passed { ExitCode::SUCCESS } else { ExitCode::FAILURE })
}

fn run_one(args: &StressArgs, files: &[PathBuf], speed: Speed, run_id: &str, stop: &AtomicBool) -> Result<RunResult> {
    let socket = sender_socket(args.target)?;
    socket.set_read_timeout(Some(Duration::from_millis(250)))?;
    let info = json!({
        "recording": args.recording,
        "speed": speed.to_string(),
        "skip_secs": args.window.skip_secs,
        "limit_secs": args.window.limit_secs,
        "sender_os": std::env::consts::OS,
    });
    let start = ControlMessage::StartRun { run_id: run_id.to_string(), info };
    match exchange(&socket, args.target, &start, run_id, Duration::from_secs(5))? {
        Some(ControlMessage::Started { archive, .. }) => {
            eprintln!("\n[{run_id}] started; ingest archives to {archive}")
        }
        Some(ControlMessage::Rejected { reason, .. }) => {
            return Err(format!("the ingest rejected the run: {reason}").into());
        }
        _ => {
            return Err(format!(
                "no answer from {}: is f1-ingest running there with --test-control, and is the port reachable?",
                args.target
            )
            .into());
        }
    }

    let plan = ReplayPlan { files: files.to_vec(), speed, window: args.window };
    let outcome = replay(&socket, args.target, &plan, stop, |sent, offset| {
        eprintln!("[{run_id}] {} sent, {} of recording time", sent.count, format_duration_ns(offset));
    })?;

    let end = ControlMessage::EndRun { run_id: run_id.to_string(), sent: outcome.sent };
    let verdict = match exchange(&socket, args.target, &end, run_id, Duration::from_secs(args.verdict_timeout_secs))? {
        Some(ControlMessage::Verdict(verdict)) => Some(*verdict),
        _ => None,
    };

    let effective_speed = outcome.effective_speed();
    let mut reasons = Vec::new();
    let mut outcome_kind = Outcome::Pass;
    match &verdict {
        None => reasons.push("no verdict received from the ingest".to_string()),
        Some(v) => {
            if !v.archive_lossless {
                reasons.push(format!(
                    "archive not lossless: missing {}, digest match {}, app drops {}, kernel drops {}, write errors {}",
                    v.missing.unwrap_or(0),
                    v.digest_match.unwrap_or(false),
                    v.app_dropped,
                    v.kernel_dropped.map_or("unknown".to_string(), |drops| drops.to_string()),
                    v.write_errors
                ));
            }
            if !v.consumers_kept_up {
                reasons.push(format!("consumers fell behind: {} batches missed", v.consumer_missed_batches));
            }
            if v.written_latency.p99_ms > args.max_written_p99_ms {
                reasons.push(format!(
                    "written p99 {:.3} ms exceeds {:.3} ms",
                    v.written_latency.p99_ms, args.max_written_p99_ms
                ));
            }
            if v.durable_latency.p99_ms > args.max_durable_p99_ms {
                reasons.push(format!(
                    "durable p99 {:.3} ms exceeds {:.3} ms",
                    v.durable_latency.p99_ms, args.max_durable_p99_ms
                ));
            }
        }
    }
    if outcome.send_errors > 0 {
        reasons.push(format!("{} send errors on the sender", outcome.send_errors));
    }
    if outcome.interrupted {
        reasons.push("interrupted".into());
    }
    if !reasons.is_empty() {
        outcome_kind = Outcome::Fail;
    }
    if let Speed::Factor(factor) = speed
        && effective_speed < factor * args.min_pace
        && !outcome.interrupted
    {
        reasons.push(format!("sender only achieved {effective_speed:.2}x of the requested {factor}x"));
        if outcome_kind == Outcome::Pass {
            outcome_kind = Outcome::Inconclusive;
        }
    }

    Ok(RunResult {
        run_id: run_id.to_string(),
        speed: speed.to_string(),
        effective_speed: (effective_speed * 1000.0).round() / 1000.0,
        sent: outcome.sent.count,
        send_errors: outcome.send_errors,
        sender_lateness: outcome.lateness.summary(),
        wall_s: (outcome.wall.as_secs_f64() * 1000.0).round() / 1000.0,
        interrupted: outcome.interrupted,
        verdict,
        outcome: outcome_kind,
        reasons,
    })
}

/// Sends `message` until a reply for `run_id` (other than an echo of the run
/// starting) arrives or `timeout` passes.
fn exchange(
    socket: &UdpSocket,
    target: SocketAddr,
    message: &ControlMessage,
    run_id: &str,
    timeout: Duration,
) -> Result<Option<ControlMessage>> {
    let wants_verdict = matches!(message, ControlMessage::EndRun { .. });
    let encoded = message.encode();
    let deadline = Instant::now() + timeout;
    let mut buf = vec![0u8; 65_536];
    let mut next_send = Instant::now();
    while Instant::now() < deadline {
        if Instant::now() >= next_send {
            socket.send_to(&encoded, target)?;
            next_send = Instant::now() + Duration::from_secs(1);
        }
        let Ok((len, _)) = socket.recv_from(&mut buf) else { continue };
        let Some(reply) = ControlMessage::decode(&buf[..len]) else { continue };
        let matches = match &reply {
            ControlMessage::Started { run_id: id, .. } => !wants_verdict && id == run_id,
            ControlMessage::Verdict(verdict) => wants_verdict && verdict.run_id == run_id,
            ControlMessage::Rejected { run_id: id, .. } => id == run_id,
            _ => false,
        };
        if matches {
            return Ok(Some(reply));
        }
    }
    Ok(None)
}

fn print_result(result: &RunResult) {
    let label = match result.outcome {
        Outcome::Pass => "PASS",
        Outcome::Fail => "FAIL",
        Outcome::Inconclusive => "INCONCLUSIVE",
    };
    eprintln!("[{}] {label}", result.run_id);
    for reason in &result.reasons {
        eprintln!("    - {reason}");
    }
}

fn print_table(results: &[RunResult]) {
    println!(
        "{:<7} {:>9} {:>9} {:>9} {:>8} {:>13} {:>12} {:>12} {:>9}  result",
        "speed", "effective", "sent", "archived", "missing", "drops app/k", "written p99", "durable p99", "consumers"
    );
    for run in results {
        let v = run.verdict.as_ref();
        println!(
            "{:<7} {:>8.2}x {:>9} {:>9} {:>8} {:>13} {:>9.3} ms {:>9.1} ms {:>9}  {}",
            run.speed,
            run.effective_speed,
            run.sent,
            v.map_or("-".into(), |v| v.received.count.to_string()),
            v.and_then(|v| v.missing).map_or("-".into(), |missing| missing.to_string()),
            v.map_or("-".into(), |v| format!(
                "{}/{}",
                v.app_dropped,
                v.kernel_dropped.map_or("?".to_string(), |drops| drops.to_string())
            )),
            v.map_or(0.0, |v| v.written_latency.p99_ms),
            v.map_or(0.0, |v| v.durable_latency.p99_ms),
            v.map_or("-".into(), |v| if v.consumers.is_empty() {
                "-".to_string()
            } else if v.consumers_kept_up {
                "ok".to_string()
            } else {
                "behind".to_string()
            }),
            match run.outcome {
                Outcome::Pass => "PASS",
                Outcome::Fail => "FAIL",
                Outcome::Inconclusive => "INCONCLUSIVE",
            }
        );
    }
}
