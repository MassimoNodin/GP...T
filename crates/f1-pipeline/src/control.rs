//! Stress-test control protocol carried in ordinary UDP datagrams on the
//! telemetry port, so a test exercises exactly the production socket.
//!
//! A control datagram is [`CONTROL_MAGIC`] followed by a JSON
//! [`ControlMessage`]. F1 packets begin with the little-endian packet format
//! (`0xE9 0x07` for 2025), so they can never be mistaken for control traffic.
//!
//! ```text
//! sender                               ingest (--test-control)
//!   StartRun{run_id} ───────────────▶  closes the live segment, opens tests/<run_id>/
//!                    ◀───────────────  Started{run_id, archive}
//!   recording datagrams ────────────▶  archived into the run's segments
//!   EndRun{run_id, sent digest} ────▶  fsyncs, waits for consumers, compares
//!                    ◀───────────────  Verdict{...}
//! ```

use std::net::SocketAddr;

use f1_format::Digest;
use serde::{Deserialize, Serialize};

use crate::{ConsumerReport, LatencySummary};

pub const CONTROL_MAGIC: &[u8; 8] = b"F1RCTL01";

pub fn is_control(datagram: &[u8]) -> bool {
    datagram.starts_with(CONTROL_MAGIC)
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ControlMessage {
    StartRun {
        run_id: String,
        #[serde(default)]
        info: serde_json::Value,
    },
    Started {
        run_id: String,
        archive: String,
    },
    EndRun {
        run_id: String,
        sent: Digest,
    },
    Verdict(Box<Verdict>),
    Rejected {
        run_id: String,
        reason: String,
    },
}

impl ControlMessage {
    pub fn encode(&self) -> Vec<u8> {
        let mut out = CONTROL_MAGIC.to_vec();
        serde_json::to_writer(&mut out, self).expect("control messages always serialise");
        out
    }

    pub fn decode(datagram: &[u8]) -> Option<Self> {
        serde_json::from_slice(datagram.strip_prefix(CONTROL_MAGIC)?).ok()
    }
}

/// The ingest's account of one stress-test run.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Verdict {
    pub run_id: String,
    /// Set if the run ended without an `EndRun` (a new run started, or shutdown).
    pub aborted: bool,
    pub expected: Option<Digest>,
    pub received: Digest,
    /// Datagrams sent but not archived (negative if extra datagrams arrived).
    pub missing: Option<i64>,
    pub digest_match: Option<bool>,
    pub app_dropped: u64,
    /// `None` where the operating system does not report per-socket drops.
    pub kernel_dropped: Option<u64>,
    pub kernel_rx_queue_peak_bytes: Option<u64>,
    pub write_errors: u64,
    /// Largest batch the writer took during the run.
    pub intake_peak_bytes: u64,
    pub consumer_missed_batches: u64,
    pub consumers_drained: bool,
    pub written_latency: LatencySummary,
    pub durable_latency: LatencySummary,
    pub consumers: Vec<ConsumerReport>,
    pub duration_s: f64,
    pub archive: String,
    /// Every expected datagram was archived intact and nothing in the
    /// pipeline reports a loss. Unknown kernel drops (non-Linux) are not
    /// counted as losses because the digest comparison already covers them.
    pub archive_lossless: bool,
    /// Every consumer processed every batch of the run.
    pub consumers_kept_up: bool,
}

/// Archive payload of a control record: source address length, address
/// text, then the control datagram itself.
pub(crate) fn encode_control_record(source: SocketAddr, datagram: &[u8], out: &mut Vec<u8>) {
    let address = source.to_string();
    out.clear();
    out.push(address.len() as u8);
    out.extend_from_slice(address.as_bytes());
    out.extend_from_slice(datagram);
}

pub fn decode_control_record(payload: &[u8]) -> Option<(SocketAddr, &[u8])> {
    let (&len, rest) = payload.split_first()?;
    let address = std::str::from_utf8(rest.get(..len as usize)?).ok()?.parse().ok()?;
    Some((address, &rest[len as usize..]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_messages_and_records() {
        let message = ControlMessage::EndRun { run_id: "r1".into(), sent: Digest { count: 3, sum: u64::MAX - 1 } };
        let encoded = message.encode();
        assert!(is_control(&encoded));
        assert!(!is_control(&2025u16.to_le_bytes()));
        assert_eq!(ControlMessage::decode(&encoded), Some(message));

        let mut payload = Vec::new();
        let source: SocketAddr = "192.168.1.111:50123".parse().unwrap();
        encode_control_record(source, &encoded, &mut payload);
        assert_eq!(decode_control_record(&payload), Some((source, &encoded[..])));
    }
}
