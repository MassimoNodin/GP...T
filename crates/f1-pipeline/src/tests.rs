use std::fs;
use std::net::UdpSocket;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use f1_format::{Digest, scan};
use f1_wire::{PACKET_FORMAT_2025, PacketId};
use serde_json::json;

use crate::control::{ControlMessage, Verdict};
use crate::*;

fn temp_dir() -> PathBuf {
    static NEXT: AtomicU32 = AtomicU32::new(0);
    let dir = std::env::temp_dir().join(format!(
        "f1-pipeline-test-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn packet(index: u32) -> Vec<u8> {
    let id = PacketId::ALL[(index % 16) as usize];
    let mut bytes = vec![0u8; id.size()];
    bytes[0..2].copy_from_slice(&PACKET_FORMAT_2025.to_le_bytes());
    bytes[6] = id as u8;
    bytes[7..15].copy_from_slice(&42u64.to_le_bytes());
    bytes[23..27].copy_from_slice(&index.to_le_bytes());
    bytes
}

fn config(sink: SinkConfig, test_control: bool) -> PipelineConfig {
    PipelineConfig {
        listen: "127.0.0.1:0".parse().unwrap(),
        recv_buffer_bytes: 4 << 20,
        consumer_queue_batches: 64,
        receiver: ReceiverConfig { batch_max_age: Duration::from_millis(2), ..ReceiverConfig::default() },
        writer: WriterConfig {
            sink,
            metadata: json!({"tool": "test"}),
            fsync_interval: Duration::from_millis(20),
            stats_interval: Duration::from_secs(60),
            test_control,
        },
    }
}

fn exchange(socket: &UdpSocket, target: std::net::SocketAddr, message: &ControlMessage) -> ControlMessage {
    let mut buf = vec![0u8; 65_536];
    for _ in 0..20 {
        socket.send_to(&message.encode(), target).unwrap();
        if let Ok((len, _)) = socket.recv_from(&mut buf) {
            return ControlMessage::decode(&buf[..len]).expect("valid reply");
        }
    }
    panic!("no reply to {message:?}");
}

/// Sends packets slowly enough that loopback never overflows the socket buffer.
fn send_packets(socket: &UdpSocket, target: std::net::SocketAddr, count: u32, pause_every: u32) -> Digest {
    let mut digest = Digest::default();
    for index in 0..count {
        let bytes = packet(index);
        socket.send_to(&bytes, target).unwrap();
        digest.add(&bytes);
        if index % pause_every == pause_every - 1 {
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    digest
}

fn run_test(pipeline: &Pipeline, count: u32, pause_every: u32) -> Verdict {
    let target = pipeline.local_addr();
    let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
    socket.set_read_timeout(Some(Duration::from_millis(500))).unwrap();
    let started = exchange(&socket, target, &ControlMessage::StartRun { run_id: "t1".into(), info: json!({}) });
    assert!(matches!(started, ControlMessage::Started { .. }), "{started:?}");
    let sent = send_packets(&socket, target, count, pause_every);
    match exchange(&socket, target, &ControlMessage::EndRun { run_id: "t1".into(), sent }) {
        ControlMessage::Verdict(verdict) => *verdict,
        other => panic!("expected a verdict, got {other:?}"),
    }
}

#[test]
fn stress_run_is_archived_separately_and_judged_lossless() {
    let root = temp_dir();
    let sink = SinkConfig::Archive { root: root.clone(), segment_bytes: 256 * 1024 };
    let counter: Box<dyn Consumer> = Box::new(SyntheticLoad::new(Duration::ZERO));
    let pipeline = Pipeline::start(config(sink, true), vec![counter], Box::new(|_| {})).unwrap();

    let verdict = run_test(&pipeline, 2_000, 50);
    assert!(verdict.archive_lossless, "{verdict:#?}");
    assert!(verdict.consumers_kept_up);
    // Datagrams sharing a batch with the end marker still reach the consumer before the verdict.
    assert_eq!(verdict.consumers[0].packets, 2_000);
    assert_eq!(verdict.consumers[0].latency.count, 2_000);
    assert_eq!(verdict.missing, Some(0));
    assert_eq!(verdict.received.count, 2_000);
    assert_eq!(verdict.written_latency.count, 2_000);
    assert_eq!(verdict.durable_latency.count, 2_000);

    let outcome = pipeline.stop().unwrap();
    assert_eq!(outcome.final_stats.received, 2_002);

    // The run rotated through several small segments, all complete and readable.
    let run_dir = PathBuf::from(&verdict.archive);
    assert!(run_dir.join("verdict.json").exists());
    let mut datagrams = 0;
    let mut segments = 0;
    for entry in fs::read_dir(&run_dir).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().is_some_and(|ext| ext == "f1r") {
            let report = scan(&path).unwrap();
            assert_eq!(report.end, f1_format::EndState::Complete);
            datagrams += report.rebuilt.datagrams;
            segments += 1;
        }
    }
    assert_eq!(datagrams, 2_000);
    assert!(segments > 1);
}

#[test]
fn slow_consumer_misses_batches_without_affecting_the_archive() {
    let root = temp_dir();
    let sink = SinkConfig::Archive { root, segment_bytes: 1 << 30 };
    let mut config = config(sink, true);
    config.consumer_queue_batches = 1;
    let slow: Box<dyn Consumer> = Box::new(SyntheticLoad::new(Duration::from_millis(2)));
    let pipeline = Pipeline::start(config, vec![slow], Box::new(|_| {})).unwrap();

    let verdict = run_test(&pipeline, 1_000, 20);
    assert!(verdict.archive_lossless, "{verdict:#?}");
    assert!(!verdict.consumers_kept_up);
    assert!(verdict.consumer_missed_batches > 0);
    pipeline.stop().unwrap();
}

#[test]
fn recorder_writes_one_complete_file() {
    let path = temp_dir().join("session.f1r");
    let pipeline =
        Pipeline::start(config(SinkConfig::File { path: path.clone() }, false), Vec::new(), Box::new(|_| {})).unwrap();
    let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
    let sent = send_packets(&socket, pipeline.local_addr(), 500, 50);
    std::thread::sleep(Duration::from_millis(100));
    let outcome = pipeline.stop().unwrap();
    let summary = outcome.last_footer.unwrap().summary;
    assert_eq!(summary.digest, sent);
    assert_eq!(summary.sessions.len(), 1);
    assert_eq!(scan(&path).unwrap().rebuilt, summary);
    assert!(Pipeline::start(config(SinkConfig::File { path }, false), Vec::new(), Box::new(|_| {})).is_err());
}

#[test]
fn isolated_packets_are_written_within_the_batch_age_not_the_idle_tick() {
    let mut config = config(SinkConfig::Archive { root: temp_dir(), segment_bytes: 1 << 30 }, true);
    // With these settings the writer's idle wait is 50 ms; a batch of one
    // packet must still be written after ~2 ms (plus OS timer resolution).
    config.writer.fsync_interval = Duration::from_secs(1);
    let pipeline = Pipeline::start(config, Vec::new(), Box::new(|_| {})).unwrap();
    let target = pipeline.local_addr();
    let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
    socket.set_read_timeout(Some(Duration::from_millis(500))).unwrap();
    let started = exchange(&socket, target, &ControlMessage::StartRun { run_id: "sparse".into(), info: json!({}) });
    assert!(matches!(started, ControlMessage::Started { .. }), "{started:?}");
    let mut sent = Digest::default();
    for index in 0..40 {
        let bytes = packet(index);
        socket.send_to(&bytes, target).unwrap();
        sent.add(&bytes);
        std::thread::sleep(Duration::from_millis(30));
    }
    let verdict = match exchange(&socket, target, &ControlMessage::EndRun { run_id: "sparse".into(), sent }) {
        ControlMessage::Verdict(verdict) => *verdict,
        other => panic!("expected a verdict, got {other:?}"),
    };
    assert!(verdict.archive_lossless, "{verdict:#?}");
    // Without the wake-up the median sits near half the idle wait (~25 ms).
    assert!(verdict.written_latency.p50_ms < 16.0, "{:?}", verdict.written_latency);
    pipeline.stop().unwrap();
}
