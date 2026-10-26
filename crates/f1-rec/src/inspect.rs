use std::path::{Path, PathBuf};
use std::process::ExitCode;

use f1_format::{EndState, Summary, read_footer, scan};
use f1_pipeline::utc_stamp;
use serde_json::json;

use crate::util::{Result, format_bytes, format_duration_ns, recording_files};

pub fn info(paths: &[PathBuf], as_json: bool) -> Result<ExitCode> {
    let mut documents = Vec::new();
    for path in paths {
        for file in recording_files(path)? {
            let (header, footer) = read_footer(&file)?;
            let (summary, status) = match footer {
                Some(footer) => (footer.summary, "complete".to_string()),
                None => {
                    let report = scan(&file)?;
                    let status = match (&report.corruption, &report.end) {
                        (Some(problem), _) => format!("corrupt ({problem}); summary covers blocks before it"),
                        (None, EndState::Truncated { offset }) => {
                            format!("truncated at byte {offset} (no footer); summary rebuilt")
                        }
                        _ => "unclean close (no footer); summary rebuilt".to_string(),
                    };
                    (report.rebuilt, status)
                }
            };
            if as_json {
                documents.push(json!({
                    "path": file,
                    "status": status,
                    "start_unix_ns": header.start_unix_ns,
                    "metadata": header.metadata,
                    "summary": summary,
                }));
            } else {
                println!("{}", file.display());
                println!("  status:   {status}");
                println!("  started:  {} UTC", utc_stamp(header.start_unix_ns + summary.first_ts_ns.unwrap_or(0)));
                for key in ["label", "tool", "source_file"] {
                    if let Some(value) = header.metadata.get(key).and_then(|value| value.as_str()) {
                        println!("  {key:<9} {value}");
                    }
                }
                print_summary(None, &summary);
                println!();
            }
        }
    }
    if as_json {
        println!("{}", serde_json::to_string_pretty(&documents)?);
    }
    Ok(ExitCode::SUCCESS)
}

/// Prints a summary, preceded by the recording's path if one is given.
pub fn print_summary(path: Option<&Path>, summary: &Summary) {
    if let Some(path) = path {
        println!("{}", path.display());
    }
    let span = summary.span_ns();
    let rate = if span > 0 { summary.datagrams as f64 / (span as f64 / 1e9) } else { 0.0 };
    println!(
        "  span:     {}   datagrams: {} ({rate:.1}/s, {})",
        format_duration_ns(span),
        summary.datagrams,
        format_bytes(summary.datagram_bytes)
    );
    println!(
        "  losses:   app {}  kernel {}   rejected: {}   digest: {}/{}",
        summary.app_dropped,
        summary.kernel_dropped,
        summary.rejected.total(),
        summary.digest.count,
        summary.digest.sum_hex()
    );
    for session in &summary.sessions {
        println!(
            "  session {:016x}: {} packets over {}, frames {}..{}, game {}, player car {}",
            session.session_uid,
            session.packets,
            format_duration_ns(session.last_ts_ns.saturating_sub(session.first_ts_ns)),
            session.first_overall_frame,
            session.last_overall_frame,
            session.game_version,
            session.player_car_index,
        );
        let counts: Vec<String> = session.packet_counts.iter().map(|(name, count)| format!("{name} {count}")).collect();
        println!("    {}", counts.join(", "));
    }
}

pub fn verify(paths: &[PathBuf]) -> Result<ExitCode> {
    let mut failed = false;
    for path in paths {
        for file in recording_files(path)? {
            let report = scan(&file)?;
            let mut problems = Vec::new();
            if let Some(corruption) = &report.corruption {
                problems.push(format!("corrupt block at {corruption}"));
            }
            match (&report.end, &report.footer) {
                (EndState::Complete, Some(footer)) if footer.summary != report.rebuilt => {
                    problems.push("footer summary does not match the blocks".into())
                }
                (EndState::Truncated { offset }, _) => problems.push(format!("truncated at byte {offset}")),
                _ => {}
            }
            let status = match report.end {
                EndState::Complete => "complete",
                EndState::Unclean => "unclean close (no footer)",
                EndState::Truncated { .. } => "truncated",
            };
            if problems.is_empty() {
                println!(
                    "OK    {} ({status}, {} blocks, {} datagrams)",
                    file.display(),
                    report.blocks,
                    report.rebuilt.datagrams
                );
            } else {
                failed = true;
                println!("FAIL  {}: {}", file.display(), problems.join("; "));
            }
        }
    }
    Ok(if failed { ExitCode::FAILURE } else { ExitCode::SUCCESS })
}
