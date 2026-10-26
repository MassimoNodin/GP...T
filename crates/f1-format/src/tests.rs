use std::fs::{self, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};

use f1_wire::{PACKET_FORMAT_2025, PacketId};
use serde_json::json;

use super::*;

fn temp_path(name: &str) -> PathBuf {
    static NEXT: AtomicU32 = AtomicU32::new(0);
    let dir = std::env::temp_dir().join(format!(
        "f1-format-test-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

fn packet(id: PacketId, session_uid: u64, frame: u32) -> Vec<u8> {
    let mut bytes = vec![0u8; id.size()];
    bytes[0..2].copy_from_slice(&PACKET_FORMAT_2025.to_le_bytes());
    bytes[2] = 25;
    bytes[3] = 1;
    bytes[4] = 7;
    bytes[6] = id as u8;
    bytes[7..15].copy_from_slice(&session_uid.to_le_bytes());
    bytes[23..27].copy_from_slice(&frame.to_le_bytes());
    bytes[100 % id.size()] = frame as u8;
    bytes
}

fn write_sample(path: &std::path::Path) -> Vec<Vec<u8>> {
    let mut writer = RecordingWriter::create(path, 1_000, &json!({"label": "test"})).unwrap();
    let mut sent = Vec::new();
    for frame in 0..500u32 {
        let id = if frame % 2 == 0 { PacketId::CarTelemetry } else { PacketId::Motion };
        let uid = if frame < 300 { 7 } else { 9 };
        let bytes = packet(id, uid, frame);
        writer.push(u64::from(frame) * 1_000_000, RecordKind::DATAGRAM, &bytes).unwrap();
        sent.push(bytes);
    }
    writer.push(600_000_000, RecordKind::LOSS, &LossCounts { app_dropped: 2, kernel_dropped: 3 }.encode()).unwrap();
    writer.push(601_000_000, RecordKind::DATAGRAM, b"junk").unwrap();
    sent.push(b"junk".to_vec());
    writer.finish(json!({"reason": "test"})).unwrap();
    sent
}

#[test]
fn round_trips_records_and_footer() {
    let path = temp_path("a.f1r");
    let sent = write_sample(&path);

    let mut reader = RecordingReader::open(&path).unwrap();
    assert_eq!(reader.header().start_unix_ns, 1_000);
    assert_eq!(reader.header().metadata["label"], "test");
    let mut body = Vec::new();
    let mut datagrams = Vec::new();
    while reader.next_block(&mut body).unwrap().is_some() {
        for record in Records::new(&body) {
            if record.kind == RecordKind::DATAGRAM {
                datagrams.push(record.payload.to_vec());
            }
        }
    }
    assert_eq!(reader.end_state(), Some(&EndState::Complete));
    assert_eq!(datagrams, sent);

    let summary = &reader.footer().unwrap().summary;
    assert_eq!(summary.datagrams, 501);
    assert_eq!(summary.records, 502);
    assert_eq!(summary.app_dropped, 2);
    assert_eq!(summary.kernel_dropped, 3);
    assert_eq!(summary.rejected.too_short, 1);
    assert_eq!(summary.sessions.len(), 2);
    assert_eq!(summary.sessions[0].session_uid, 7);
    assert_eq!(summary.sessions[0].packets, 300);
    assert_eq!(summary.sessions[0].packet_counts["car_telemetry"], 150);
    assert_eq!(summary.sessions[1].first_overall_frame, 300);
    assert_eq!(summary.sessions[1].last_overall_frame, 499);
    assert_eq!(summary.sessions[1].game_version, "1.07");

    let (_, fast) = read_footer(&path).unwrap();
    assert_eq!(fast.as_ref(), reader.footer());
    assert_eq!(scan(&path).unwrap().rebuilt, *summary);
}

#[test]
fn digest_ignores_order() {
    let mut a = Digest::default();
    let mut b = Digest::default();
    for payload in [&b"one"[..], b"two", b"three"] {
        a.add(payload);
    }
    for payload in [&b"three"[..], b"one", b"two"] {
        b.add(payload);
    }
    assert_eq!(a, b);
    b.add(b"four");
    assert_ne!(a, b);
}

#[test]
fn reads_unclean_and_truncated_recordings() {
    let path = temp_path("unclean.f1r");
    let mut writer = RecordingWriter::create(&path, 0, &json!({})).unwrap();
    let mut body = Vec::new();
    encode_record(&mut body, 5, RecordKind::DATAGRAM, &packet(PacketId::Event, 1, 1));
    writer.write_block(&body).unwrap();
    writer.write_block(&body).unwrap();
    writer.sync().unwrap();
    drop(writer);

    let report = scan(&path).unwrap();
    assert_eq!(report.end, EndState::Unclean);
    assert_eq!(report.rebuilt.datagrams, 2);
    assert!(read_footer(&path).unwrap().1.is_none());

    let len = fs::metadata(&path).unwrap().len();
    OpenOptions::new().write(true).open(&path).unwrap().set_len(len - 3).unwrap();
    let report = scan(&path).unwrap();
    assert!(matches!(report.end, EndState::Truncated { .. }));
    assert_eq!(report.rebuilt.datagrams, 1);
}

#[test]
fn detects_corrupt_blocks() {
    let path = temp_path("corrupt.f1r");
    write_sample(&path);
    let mut file = OpenOptions::new().write(true).open(&path).unwrap();
    let data_offset = RecordingReader::open(&path).unwrap().header().data_offset;
    file.seek(SeekFrom::Start(data_offset + 100)).unwrap();
    file.write_all(&[0xff, 0xfe]).unwrap();
    drop(file);
    let report = scan(&path).unwrap();
    assert!(report.corruption.unwrap().contains("checksum"));
    assert_eq!(report.blocks, 0);
}

#[test]
fn refuses_to_overwrite() {
    let path = temp_path("exists.f1r");
    write_sample(&path);
    assert!(RecordingWriter::create(&path, 0, &json!({})).is_err());
}
