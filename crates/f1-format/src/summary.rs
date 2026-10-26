use std::collections::{BTreeMap, HashMap};

use f1_wire::{PACKET_ID_COUNT, PacketId, Rejection};
use serde::{Deserialize, Serialize};
use xxhash_rust::xxh3::xxh3_64;

use crate::{LossCounts, Record, RecordKind};

/// Order-independent fingerprint of a multiset of datagram payloads: the
/// count plus the wrapping sum of each payload's XXH3-64 hash. Two sides that
/// saw the same datagrams, in any order, produce the same digest.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Digest {
    pub count: u64,
    #[serde(with = "hex_u64")]
    pub sum: u64,
}

impl Digest {
    pub fn add(&mut self, payload: &[u8]) {
        self.count += 1;
        self.sum = self.sum.wrapping_add(xxh3_64(payload));
    }

    pub fn merge(&mut self, other: Digest) {
        self.count += other.count;
        self.sum = self.sum.wrapping_add(other.sum);
    }

    pub fn sum_hex(&self) -> String {
        format!("{:016x}", self.sum)
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Rejected {
    pub too_short: u64,
    pub unsupported_format: u64,
    pub unknown_packet_id: u64,
    pub size_mismatch: u64,
}

impl Rejected {
    pub fn total(&self) -> u64 {
        self.too_short + self.unsupported_format + self.unknown_packet_id + self.size_mismatch
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionSummary {
    #[serde(with = "hex_u64")]
    pub session_uid: u64,
    pub packets: u64,
    pub first_ts_ns: u64,
    pub last_ts_ns: u64,
    pub first_session_time: f32,
    pub last_session_time: f32,
    pub first_overall_frame: u32,
    pub last_overall_frame: u32,
    pub player_car_index: u8,
    pub game_version: String,
    pub packet_counts: BTreeMap<String, u64>,
}

/// Everything known about a recording's contents, built from record headers.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Summary {
    pub records: u64,
    pub datagrams: u64,
    pub datagram_bytes: u64,
    pub control_records: u64,
    pub loss_records: u64,
    pub other_records: u64,
    /// Totals from loss records: datagrams the receiver knows it did not keep.
    pub app_dropped: u64,
    pub kernel_dropped: u64,
    /// Datagrams archived but not recognised as F1 25 packets.
    pub rejected: Rejected,
    pub first_ts_ns: Option<u64>,
    pub last_ts_ns: Option<u64>,
    pub digest: Digest,
    pub sessions: Vec<SessionSummary>,
}

impl Summary {
    /// Recorded time span in nanoseconds.
    pub fn span_ns(&self) -> u64 {
        match (self.first_ts_ns, self.last_ts_ns) {
            (Some(first), Some(last)) => last.saturating_sub(first),
            _ => 0,
        }
    }
}

/// Written after the last block of a cleanly closed recording.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Footer {
    pub summary: Summary,
    pub closed_unix_ns: u64,
    #[serde(default)]
    pub extra: serde_json::Value,
}

#[derive(Debug, Clone)]
struct SessionAcc {
    uid: u64,
    packets: u64,
    counts: [u64; PACKET_ID_COUNT],
    first_ts_ns: u64,
    last_ts_ns: u64,
    first_session_time: f32,
    last_session_time: f32,
    first_overall_frame: u32,
    last_overall_frame: u32,
    player_car_index: u8,
    game_version: (u8, u8),
}

/// Incrementally builds a [`Summary`] while records are written or read.
#[derive(Debug, Clone, Default)]
pub struct Indexer {
    summary: Summary,
    sessions: Vec<SessionAcc>,
    positions: HashMap<u64, usize>,
    last_session: Option<(u64, usize)>,
}

impl Indexer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn observe(&mut self, record: &Record<'_>) {
        let s = &mut self.summary;
        s.records += 1;
        s.first_ts_ns.get_or_insert(record.ts_ns);
        s.last_ts_ns = Some(record.ts_ns);
        match record.kind {
            RecordKind::DATAGRAM => self.observe_datagram(record.ts_ns, record.payload),
            RecordKind::CONTROL => s.control_records += 1,
            RecordKind::LOSS => {
                s.loss_records += 1;
                if let Ok(loss) = LossCounts::decode(record.payload) {
                    s.app_dropped += u64::from(loss.app_dropped);
                    s.kernel_dropped += u64::from(loss.kernel_dropped);
                }
            }
            _ => s.other_records += 1,
        }
    }

    fn observe_datagram(&mut self, ts_ns: u64, payload: &[u8]) {
        let s = &mut self.summary;
        s.datagrams += 1;
        s.datagram_bytes += payload.len() as u64;
        s.digest.add(payload);
        let (header, id) = match f1_wire::inspect(payload) {
            Ok(found) => found,
            Err(rejection) => {
                match rejection {
                    Rejection::TooShort { .. } => s.rejected.too_short += 1,
                    Rejection::UnsupportedFormat { .. } => s.rejected.unsupported_format += 1,
                    Rejection::UnknownPacketId { .. } => s.rejected.unknown_packet_id += 1,
                    Rejection::SizeMismatch { .. } => s.rejected.size_mismatch += 1,
                }
                return;
            }
        };
        let uid = header.session_uid;
        let position = match self.last_session {
            Some((last_uid, position)) if last_uid == uid => position,
            _ => {
                let position = *self.positions.entry(uid).or_insert_with(|| {
                    self.sessions.push(SessionAcc {
                        uid,
                        packets: 0,
                        counts: [0; PACKET_ID_COUNT],
                        first_ts_ns: ts_ns,
                        last_ts_ns: ts_ns,
                        first_session_time: header.session_time,
                        last_session_time: header.session_time,
                        first_overall_frame: header.overall_frame_identifier,
                        last_overall_frame: header.overall_frame_identifier,
                        player_car_index: header.player_car_index,
                        game_version: (header.game_major_version, header.game_minor_version),
                    });
                    self.sessions.len() - 1
                });
                self.last_session = Some((uid, position));
                position
            }
        };
        let acc = &mut self.sessions[position];
        acc.packets += 1;
        acc.counts[id.index()] += 1;
        acc.last_ts_ns = ts_ns;
        acc.last_session_time = header.session_time;
        acc.last_overall_frame = header.overall_frame_identifier;
        acc.player_car_index = header.player_car_index;
    }

    pub fn digest(&self) -> Digest {
        self.summary.digest
    }

    pub fn records(&self) -> u64 {
        self.summary.records
    }

    pub fn datagrams(&self) -> u64 {
        self.summary.datagrams
    }

    pub fn summary(&self) -> Summary {
        let mut summary = self.summary.clone();
        summary.sessions = self
            .sessions
            .iter()
            .map(|acc| SessionSummary {
                session_uid: acc.uid,
                packets: acc.packets,
                first_ts_ns: acc.first_ts_ns,
                last_ts_ns: acc.last_ts_ns,
                first_session_time: acc.first_session_time,
                last_session_time: acc.last_session_time,
                first_overall_frame: acc.first_overall_frame,
                last_overall_frame: acc.last_overall_frame,
                player_car_index: acc.player_car_index,
                game_version: format!("{}.{:02}", acc.game_version.0, acc.game_version.1),
                packet_counts: PacketId::ALL
                    .iter()
                    .filter(|id| acc.counts[id.index()] > 0)
                    .map(|id| (id.name().to_string(), acc.counts[id.index()]))
                    .collect(),
            })
            .collect();
        summary
    }
}

/// Serialises a `u64` as a 16-digit hex string so JSON readers that use
/// doubles (JavaScript, jq) do not lose precision.
mod hex_u64 {
    use serde::{Deserialize, Deserializer, Serializer, de::Error};

    pub fn serialize<S: Serializer>(value: &u64, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&format!("{value:016x}"))
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(deserializer: D) -> Result<u64, D::Error> {
        let text = String::deserialize(deserializer)?;
        u64::from_str_radix(&text, 16).map_err(D::Error::custom)
    }
}
