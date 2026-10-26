//! F1 25 UDP telemetry wire format (packet format 2025).
//!
//! This crate only understands the 29-byte packet header and the catalogue of
//! packet types and sizes. Body decoders for individual packet types belong
//! here as they are needed; until then the pipeline archives bodies untouched.

use std::fmt;

/// `m_packetFormat` value sent by F1 25.
pub const PACKET_FORMAT_2025: u16 = 2025;

/// Size of the common packet header in bytes.
pub const HEADER_LEN: usize = 29;

/// Number of packet types defined by packet format 2025.
pub const PACKET_ID_COUNT: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(u8)]
pub enum PacketId {
    Motion = 0,
    Session = 1,
    LapData = 2,
    Event = 3,
    Participants = 4,
    CarSetups = 5,
    CarTelemetry = 6,
    CarStatus = 7,
    FinalClassification = 8,
    LobbyInfo = 9,
    CarDamage = 10,
    SessionHistory = 11,
    TyreSets = 12,
    MotionEx = 13,
    TimeTrial = 14,
    LapPositions = 15,
}

impl PacketId {
    pub const ALL: [PacketId; PACKET_ID_COUNT] = [
        PacketId::Motion,
        PacketId::Session,
        PacketId::LapData,
        PacketId::Event,
        PacketId::Participants,
        PacketId::CarSetups,
        PacketId::CarTelemetry,
        PacketId::CarStatus,
        PacketId::FinalClassification,
        PacketId::LobbyInfo,
        PacketId::CarDamage,
        PacketId::SessionHistory,
        PacketId::TyreSets,
        PacketId::MotionEx,
        PacketId::TimeTrial,
        PacketId::LapPositions,
    ];

    pub const fn from_u8(value: u8) -> Option<Self> {
        if (value as usize) < PACKET_ID_COUNT { Some(Self::ALL[value as usize]) } else { None }
    }

    pub const fn index(self) -> usize {
        self as usize
    }

    /// Full datagram size, header included, for packet format 2025.
    pub const fn size(self) -> usize {
        match self {
            PacketId::Motion => 1349,
            PacketId::Session => 753,
            PacketId::LapData => 1285,
            PacketId::Event => 45,
            PacketId::Participants => 1284,
            PacketId::CarSetups => 1133,
            PacketId::CarTelemetry => 1352,
            PacketId::CarStatus => 1239,
            PacketId::FinalClassification => 1042,
            PacketId::LobbyInfo => 954,
            PacketId::CarDamage => 1041,
            PacketId::SessionHistory => 1460,
            PacketId::TyreSets => 231,
            PacketId::MotionEx => 273,
            PacketId::TimeTrial => 101,
            PacketId::LapPositions => 1131,
        }
    }

    pub const fn name(self) -> &'static str {
        match self {
            PacketId::Motion => "motion",
            PacketId::Session => "session",
            PacketId::LapData => "lap_data",
            PacketId::Event => "event",
            PacketId::Participants => "participants",
            PacketId::CarSetups => "car_setups",
            PacketId::CarTelemetry => "car_telemetry",
            PacketId::CarStatus => "car_status",
            PacketId::FinalClassification => "final_classification",
            PacketId::LobbyInfo => "lobby_info",
            PacketId::CarDamage => "car_damage",
            PacketId::SessionHistory => "session_history",
            PacketId::TyreSets => "tyre_sets",
            PacketId::MotionEx => "motion_ex",
            PacketId::TimeTrial => "time_trial",
            PacketId::LapPositions => "lap_positions",
        }
    }
}

impl fmt::Display for PacketId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// The common header that starts every packet (little-endian, packed).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PacketHeader {
    pub packet_format: u16,
    pub game_year: u8,
    pub game_major_version: u8,
    pub game_minor_version: u8,
    pub packet_version: u8,
    pub packet_id: u8,
    pub session_uid: u64,
    pub session_time: f32,
    pub frame_identifier: u32,
    pub overall_frame_identifier: u32,
    pub player_car_index: u8,
    pub secondary_player_car_index: u8,
}

impl PacketHeader {
    /// Reads the header layout without checking the format or packet id.
    pub fn parse(bytes: &[u8]) -> Option<Self> {
        let h: &[u8; HEADER_LEN] = bytes.get(..HEADER_LEN)?.try_into().ok()?;
        Some(Self {
            packet_format: u16::from_le_bytes([h[0], h[1]]),
            game_year: h[2],
            game_major_version: h[3],
            game_minor_version: h[4],
            packet_version: h[5],
            packet_id: h[6],
            session_uid: u64::from_le_bytes(h[7..15].try_into().unwrap()),
            session_time: f32::from_le_bytes(h[15..19].try_into().unwrap()),
            frame_identifier: u32::from_le_bytes(h[19..23].try_into().unwrap()),
            overall_frame_identifier: u32::from_le_bytes(h[23..27].try_into().unwrap()),
            player_car_index: h[27],
            secondary_player_car_index: h[28],
        })
    }
}

/// Why a datagram is not a recognised F1 25 packet.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rejection {
    TooShort { len: usize },
    UnsupportedFormat { packet_format: u16 },
    UnknownPacketId { packet_id: u8 },
    SizeMismatch { packet_id: PacketId, expected: usize, actual: usize },
}

impl fmt::Display for Rejection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Rejection::TooShort { len } => write!(f, "datagram of {len} bytes is shorter than the header"),
            Rejection::UnsupportedFormat { packet_format } => {
                write!(f, "unsupported packet format {packet_format}")
            }
            Rejection::UnknownPacketId { packet_id } => write!(f, "unknown packet id {packet_id}"),
            Rejection::SizeMismatch { packet_id, expected, actual } => {
                write!(f, "{packet_id} packet is {actual} bytes, expected {expected}")
            }
        }
    }
}

impl std::error::Error for Rejection {}

/// Validates a datagram as an F1 25 packet and returns its header and type.
pub fn inspect(datagram: &[u8]) -> Result<(PacketHeader, PacketId), Rejection> {
    let header = PacketHeader::parse(datagram).ok_or(Rejection::TooShort { len: datagram.len() })?;
    if header.packet_format != PACKET_FORMAT_2025 {
        return Err(Rejection::UnsupportedFormat { packet_format: header.packet_format });
    }
    let id = PacketId::from_u8(header.packet_id).ok_or(Rejection::UnknownPacketId { packet_id: header.packet_id })?;
    if datagram.len() != id.size() {
        return Err(Rejection::SizeMismatch { packet_id: id, expected: id.size(), actual: datagram.len() });
    }
    Ok((header, id))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn packet(id: PacketId) -> Vec<u8> {
        let mut bytes = vec![0u8; id.size()];
        bytes[0..2].copy_from_slice(&PACKET_FORMAT_2025.to_le_bytes());
        bytes[2] = 25;
        bytes[3] = 1;
        bytes[4] = 2;
        bytes[5] = 1;
        bytes[6] = id as u8;
        bytes[7..15].copy_from_slice(&0x0123_4567_89ab_cdefu64.to_le_bytes());
        bytes[15..19].copy_from_slice(&12.5f32.to_le_bytes());
        bytes[19..23].copy_from_slice(&100u32.to_le_bytes());
        bytes[23..27].copy_from_slice(&105u32.to_le_bytes());
        bytes[27] = 3;
        bytes[28] = 255;
        bytes
    }

    #[test]
    fn parses_every_header_field() {
        let (header, id) = inspect(&packet(PacketId::CarTelemetry)).unwrap();
        assert_eq!(id, PacketId::CarTelemetry);
        assert_eq!(header.game_year, 25);
        assert_eq!(header.game_major_version, 1);
        assert_eq!(header.game_minor_version, 2);
        assert_eq!(header.packet_version, 1);
        assert_eq!(header.session_uid, 0x0123_4567_89ab_cdef);
        assert_eq!(header.session_time, 12.5);
        assert_eq!(header.frame_identifier, 100);
        assert_eq!(header.overall_frame_identifier, 105);
        assert_eq!(header.player_car_index, 3);
        assert_eq!(header.secondary_player_car_index, 255);
    }

    #[test]
    fn accepts_every_packet_type_at_its_size() {
        for id in PacketId::ALL {
            assert_eq!(inspect(&packet(id)).unwrap().1, id);
            assert_eq!(PacketId::from_u8(id as u8), Some(id));
        }
        assert_eq!(PacketId::from_u8(16), None);
    }

    #[test]
    fn rejects_malformed_datagrams() {
        assert_eq!(inspect(&[0u8; 10]), Err(Rejection::TooShort { len: 10 }));

        let mut old = packet(PacketId::Motion);
        old[0..2].copy_from_slice(&2024u16.to_le_bytes());
        assert_eq!(inspect(&old), Err(Rejection::UnsupportedFormat { packet_format: 2024 }));

        let mut unknown = packet(PacketId::Event);
        unknown[6] = 42;
        assert_eq!(inspect(&unknown), Err(Rejection::UnknownPacketId { packet_id: 42 }));

        let mut short = packet(PacketId::Session);
        short.pop();
        assert_eq!(
            inspect(&short),
            Err(Rejection::SizeMismatch { packet_id: PacketId::Session, expected: 753, actual: 752 })
        );
    }
}
