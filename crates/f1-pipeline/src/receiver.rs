use std::io;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use f1_format::{LossCounts, RecordKind};

use crate::control::{encode_control_record, is_control};
use crate::{Batch, Clock, Metrics};

#[derive(Debug, Clone)]
pub struct ReceiverConfig {
    /// The writer takes the pending batch once it holds this many bytes...
    pub batch_max_bytes: usize,
    /// ...or once its oldest record is this old.
    pub batch_max_age: Duration,
    /// While the writer is busy the pending batch keeps growing up to this
    /// size; only beyond it are datagrams dropped (and counted).
    pub batch_overflow_bytes: usize,
    /// Re-send every datagram to this address as soon as it arrives.
    pub forward: Option<SocketAddr>,
}

impl Default for ReceiverConfig {
    fn default() -> Self {
        Self {
            batch_max_bytes: 256 * 1024,
            batch_max_age: Duration::from_millis(10),
            batch_overflow_bytes: 32 * 1024 * 1024,
            forward: None,
        }
    }
}

/// Hand-over point between the receiver and the writer: the batch being
/// filled. The receiver only ever holds the lock to append one record.
pub(crate) struct Intake {
    state: Mutex<IntakeState>,
    ready: Condvar,
    max_bytes: usize,
    max_age_ns: u64,
    overflow_bytes: usize,
    capacity: usize,
}

struct IntakeState {
    batch: Batch,
    pending_loss: u32,
    closed: bool,
}

pub(crate) enum Taken {
    Batch(Batch),
    Nothing,
    Closed,
}

impl Intake {
    pub fn new(config: &ReceiverConfig) -> Self {
        let capacity = config.batch_max_bytes + 2048;
        Self {
            state: Mutex::new(IntakeState { batch: Batch::with_capacity(capacity), pending_loss: 0, closed: false }),
            ready: Condvar::new(),
            max_bytes: config.batch_max_bytes,
            max_age_ns: config.batch_max_age.as_nanos() as u64,
            overflow_bytes: config.batch_overflow_bytes,
            capacity,
        }
    }

    fn push(&self, ts: u64, kind: RecordKind, payload: &[u8], metrics: &Metrics) {
        let mut state = self.state.lock().unwrap();
        if state.batch.len_bytes() >= self.overflow_bytes {
            state.pending_loss = state.pending_loss.saturating_add(1);
            drop(state);
            Metrics::add(&metrics.app_dropped, 1);
            return;
        }
        if state.pending_loss > 0 {
            let loss = std::mem::take(&mut state.pending_loss);
            state.batch.push(ts, RecordKind::LOSS, &LossCounts { app_dropped: loss, kernel_dropped: 0 }.encode());
        }
        state.batch.push(ts, kind, payload);
        // Signal on arrival rather than leaving it to the writer's timed
        // wait, whose resolution is coarse on some platforms (~15 ms on Windows).
        let due =
            state.batch.len_bytes() >= self.max_bytes || ts.saturating_sub(state.batch.first_ts_ns) >= self.max_age_ns;
        drop(state);
        if due {
            self.ready.notify_one();
        }
    }

    fn close(&self) {
        self.state.lock().unwrap().closed = true;
        self.ready.notify_one();
    }

    /// Waits up to `wait` for the pending batch to become due (full, old
    /// enough, or the receiver closed) and takes it.
    pub fn take(&self, clock: &Clock, wait: Duration) -> Taken {
        let max_age_ns = self.max_age_ns;
        let mut state = self.state.lock().unwrap();
        let due = |state: &IntakeState| {
            state.closed
                || state.batch.len_bytes() >= self.max_bytes
                || (!state.batch.is_empty() && clock.now_ns().saturating_sub(state.batch.first_ts_ns) >= max_age_ns)
        };
        if !due(&state) {
            let wait = if state.batch.is_empty() {
                wait
            } else {
                let age = clock.now_ns().saturating_sub(state.batch.first_ts_ns);
                wait.min(Duration::from_nanos(max_age_ns.saturating_sub(age)))
            };
            state = self.ready.wait_timeout(state, wait).unwrap().0;
            if !due(&state) {
                return Taken::Nothing;
            }
        }
        if state.batch.is_empty() {
            return if state.closed { Taken::Closed } else { Taken::Nothing };
        }
        if state.pending_loss > 0 {
            let loss = std::mem::take(&mut state.pending_loss);
            let ts = clock.now_ns();
            state.batch.push(ts, RecordKind::LOSS, &LossCounts { app_dropped: loss, kernel_dropped: 0 }.encode());
        }
        Taken::Batch(std::mem::replace(&mut state.batch, Batch::with_capacity(self.capacity)))
    }
}

pub(crate) struct Receiver {
    pub socket: UdpSocket,
    pub config: ReceiverConfig,
    pub clock: Clock,
    pub metrics: Arc<Metrics>,
    pub intake: Arc<Intake>,
    pub shutdown: Arc<AtomicBool>,
}

impl Receiver {
    /// Blocks in `recv_from` without a timeout: on Windows a timed-out
    /// receive can discard a datagram arriving at that moment, so the
    /// receiver is instead woken for shutdown by [`wake`].
    pub fn run(self) {
        let Receiver { socket, config, clock, metrics, intake, shutdown } = self;
        let forward = config.forward.and_then(|target| {
            let bind: SocketAddr = if target.is_ipv4() { "0.0.0.0:0" } else { "[::]:0" }.parse().unwrap();
            UdpSocket::bind(bind).ok().map(|socket| (socket, target))
        });
        if config.forward.is_some() && forward.is_none() {
            eprintln!("warning: could not open the forwarding socket; forwarding disabled");
        }
        let mut scratch = vec![0u8; 65_536];
        let mut control_payload = Vec::new();

        loop {
            match socket.recv_from(&mut scratch) {
                Ok((len, source)) => {
                    if shutdown.load(Ordering::Relaxed) {
                        break;
                    }
                    let ts = clock.now_ns();
                    let datagram = &scratch[..len];
                    Metrics::add(&metrics.received, 1);
                    Metrics::add(&metrics.received_bytes, len as u64);
                    if let Some((forward_socket, target)) = &forward {
                        match forward_socket.send_to(datagram, target) {
                            Ok(_) => Metrics::add(&metrics.forwarded, 1),
                            Err(_) => Metrics::add(&metrics.forward_errors, 1),
                        }
                    }
                    if is_control(datagram) {
                        Metrics::add(&metrics.control_received, 1);
                        encode_control_record(source, datagram, &mut control_payload);
                        intake.push(ts, RecordKind::CONTROL, &control_payload, &metrics);
                    } else {
                        intake.push(ts, RecordKind::DATAGRAM, datagram, &metrics);
                    }
                }
                // Windows reports ICMP port-unreachable for earlier sends from
                // this socket (control replies) as a failed receive; nothing is lost.
                Err(err) if matches!(err.kind(), io::ErrorKind::ConnectionReset | io::ErrorKind::Interrupted) => {}
                Err(err) => {
                    if shutdown.load(Ordering::Relaxed) {
                        break;
                    }
                    if Metrics::get(&metrics.recv_errors) == 0 {
                        eprintln!("receive error: {err}");
                    }
                    Metrics::add(&metrics.recv_errors, 1);
                    std::thread::sleep(Duration::from_millis(1));
                }
            }
        }
        intake.close();
    }
}

/// Unblocks a receiver waiting on `local` by sending it empty datagrams.
pub(crate) fn wake(local: SocketAddr) {
    let target = match local.ip() {
        IpAddr::V4(ip) if ip.is_unspecified() => SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), local.port()),
        IpAddr::V6(ip) if ip.is_unspecified() => SocketAddr::new(IpAddr::V6(Ipv6Addr::LOCALHOST), local.port()),
        _ => local,
    };
    let bind = if target.is_ipv4() { "0.0.0.0:0" } else { "[::]:0" };
    if let Ok(socket) = UdpSocket::bind(bind) {
        for _ in 0..3 {
            let _ = socket.send_to(&[], target);
        }
    }
}
