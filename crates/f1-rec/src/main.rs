//! `f1-rec`: record, inspect, convert and replay F1 25 telemetry recordings,
//! and stress-test a running `f1-ingest` with them.

mod convert;
mod inspect;
mod record;
mod replay;
mod stress;
mod util;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

use crate::replay::{Speed, Window};

#[derive(Parser, Debug)]
#[command(name = "f1-rec", version, about = "Record, inspect and replay F1 25 telemetry")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Capture live telemetry into a .f1r recording, optionally forwarding it.
    Record(record::RecordArgs),
    /// Send a recording's datagrams to a UDP target with the original timing.
    Replay {
        /// A .f1r file, or a directory of archive segments.
        recording: PathBuf,
        /// Destination, e.g. 192.168.1.115:20777.
        #[arg(long)]
        target: SocketAddr,
        /// Playback speed: a multiplier such as 1, 2 or 0.5, or "max" for no pacing.
        #[arg(long, default_value = "1")]
        speed: Speed,
        #[command(flatten)]
        window: Window,
    },
    /// Replay a recording through a running `f1-ingest --test-control` and
    /// judge whether every datagram was archived, at one or more speeds.
    Stress(stress::StressArgs),
    /// Convert a legacy F1ECAP01 capture (.f1ecap) into a .f1r recording.
    Convert {
        input: PathBuf,
        output: PathBuf,
        /// Short description stored in the recording's metadata.
        #[arg(long)]
        label: Option<String>,
    },
    /// Summarise recordings: sessions, packet counts, rates and losses.
    Info {
        /// .f1r files or directories of them.
        #[arg(required = true)]
        paths: Vec<PathBuf>,
        /// Print the summary as JSON.
        #[arg(long)]
        json: bool,
    },
    /// Read every block, check checksums and compare against the footer.
    Verify {
        #[arg(required = true)]
        paths: Vec<PathBuf>,
    },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let result = match cli.command {
        Command::Record(args) => record::run(args),
        Command::Replay { recording, target, speed, window } => replay::run_cli(&recording, target, speed, window),
        Command::Stress(args) => stress::run(args),
        Command::Convert { input, output, label } => convert::run(&input, &output, label),
        Command::Info { paths, json } => inspect::info(&paths, json),
        Command::Verify { paths } => inspect::verify(&paths),
    };
    match result {
        Ok(code) => code,
        Err(err) => {
            eprintln!("f1-rec: {err}");
            ExitCode::FAILURE
        }
    }
}
