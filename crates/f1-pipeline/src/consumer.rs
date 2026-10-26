//! Consumers are where packet decoding and future features run. Each one has
//! its own thread and bounded queue fed with batches after they are written,
//! so a slow consumer can only hurt itself: it misses batches (counted) and
//! its lag shows up in its latency, while the archive stays complete.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use f1_format::{RecordKind, Records};
use f1_wire::{PacketHeader, PacketId};
use serde::{Deserialize, Serialize};

use crate::histogram::LatencySet;
use crate::{Batch, Clock, LatencySummary};

pub trait Consumer: Send + 'static {
    fn name(&self) -> String;

    /// Called for every recognised F1 25 packet, in arrival order.
    fn on_packet(&mut self, ts_ns: u64, header: &PacketHeader, id: PacketId, payload: &[u8]);
}

/// Burns a fixed amount of CPU per packet to model the cost of a feature
/// before it exists, e.g. "can we afford 50 µs per packet?".
pub struct SyntheticLoad {
    cost: Duration,
}

impl SyntheticLoad {
    pub fn new(cost: Duration) -> Self {
        Self { cost }
    }
}

impl Consumer for SyntheticLoad {
    fn name(&self) -> String {
        format!("synthetic-{}us", self.cost.as_micros())
    }

    fn on_packet(&mut self, _ts_ns: u64, _header: &PacketHeader, _id: PacketId, _payload: &[u8]) {
        let start = Instant::now();
        while start.elapsed() < self.cost {
            std::hint::spin_loop();
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ConsumerReport {
    pub name: String,
    /// Packets processed in the reported scope (stats interval or test run).
    pub packets: u64,
    /// Batches and datagrams skipped in the scope because the consumer's queue was full.
    pub missed_batches: u64,
    pub missed_datagrams: u64,
    /// Receive-to-processed latency per packet.
    pub latency: LatencySummary,
}

#[derive(Default)]
struct Shared {
    submitted: AtomicU64,
    completed: AtomicU64,
    missed_batches: AtomicU64,
    missed_datagrams: AtomicU64,
    latency: Mutex<LatencySet>,
    /// Missed counts at the start of the current interval and run.
    baselines: Mutex<Baselines>,
}

#[derive(Default)]
struct Baselines {
    interval: (u64, u64),
    run: (u64, u64),
}

impl Shared {
    fn missed(&self) -> (u64, u64) {
        (self.missed_batches.load(Ordering::Relaxed), self.missed_datagrams.load(Ordering::Relaxed))
    }
}

struct Slot {
    name: String,
    tx: Option<SyncSender<Arc<Batch>>>,
    shared: Arc<Shared>,
    handle: Option<JoinHandle<()>>,
}

pub(crate) struct ConsumerHub {
    slots: Vec<Slot>,
}

#[derive(Clone, Copy)]
pub(crate) enum Scope {
    Interval,
    Run,
}

impl ConsumerHub {
    pub fn start(consumers: Vec<Box<dyn Consumer>>, queue_batches: usize, clock: Clock) -> Self {
        let slots = consumers
            .into_iter()
            .map(|mut consumer| {
                let name = consumer.name();
                let shared = Arc::new(Shared::default());
                let (tx, rx) = mpsc::sync_channel::<Arc<Batch>>(queue_batches.max(1));
                let thread_shared = Arc::clone(&shared);
                let handle = thread::Builder::new()
                    .name(format!("consumer-{name}"))
                    .spawn(move || {
                        let mut latencies = Vec::new();
                        for batch in rx {
                            latencies.clear();
                            for record in Records::new(&batch.body) {
                                if record.kind != RecordKind::DATAGRAM {
                                    continue;
                                }
                                if let Ok((header, id)) = f1_wire::inspect(record.payload) {
                                    consumer.on_packet(record.ts_ns, &header, id, record.payload);
                                    latencies.push(clock.now_ns().saturating_sub(record.ts_ns));
                                }
                            }
                            let mut set = thread_shared.latency.lock().unwrap();
                            for &latency in &latencies {
                                set.record(latency);
                            }
                            drop(set);
                            thread_shared.completed.fetch_add(1, Ordering::Release);
                        }
                    })
                    .expect("spawn consumer thread");
                Slot { name, tx: Some(tx), shared, handle: Some(handle) }
            })
            .collect();
        Self { slots }
    }

    pub fn publish(&self, batch: Arc<Batch>) {
        if batch.datagrams == 0 {
            return;
        }
        for slot in &self.slots {
            let Some(tx) = &slot.tx else { continue };
            slot.shared.submitted.fetch_add(1, Ordering::Relaxed);
            match tx.try_send(Arc::clone(&batch)) {
                Ok(()) => {}
                Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {
                    slot.shared.submitted.fetch_sub(1, Ordering::Relaxed);
                    slot.shared.missed_batches.fetch_add(1, Ordering::Relaxed);
                    slot.shared.missed_datagrams.fetch_add(u64::from(batch.datagrams), Ordering::Relaxed);
                }
            }
        }
    }

    /// Waits until every consumer has processed everything submitted to it.
    pub fn wait_idle(&self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        loop {
            let idle = self.slots.iter().all(|slot| {
                slot.shared.completed.load(Ordering::Acquire) >= slot.shared.submitted.load(Ordering::Relaxed)
            });
            if idle {
                return true;
            }
            if Instant::now() >= deadline {
                return false;
            }
            thread::sleep(Duration::from_millis(1));
        }
    }

    pub fn missed_batches(&self) -> u64 {
        self.slots.iter().map(|slot| slot.shared.missed_batches.load(Ordering::Relaxed)).sum()
    }

    pub fn reset_run(&self) {
        for slot in &self.slots {
            slot.shared.latency.lock().unwrap().run.reset();
            slot.shared.baselines.lock().unwrap().run = slot.shared.missed();
        }
    }

    pub fn reports(&self, scope: Scope) -> Vec<ConsumerReport> {
        self.slots
            .iter()
            .map(|slot| {
                let now = slot.shared.missed();
                let mut set = slot.shared.latency.lock().unwrap();
                let mut baselines = slot.shared.baselines.lock().unwrap();
                let (latency, base) = match scope {
                    Scope::Interval => (set.take_interval(), std::mem::replace(&mut baselines.interval, now)),
                    Scope::Run => (set.run.summary(), baselines.run),
                };
                ConsumerReport {
                    name: slot.name.clone(),
                    packets: latency.count,
                    missed_batches: now.0 - base.0,
                    missed_datagrams: now.1 - base.1,
                    latency,
                }
            })
            .collect()
    }

    pub fn shutdown(&mut self) {
        for slot in &mut self.slots {
            slot.tx = None;
        }
        for slot in &mut self.slots {
            if let Some(handle) = slot.handle.take() {
                let _ = handle.join();
            }
        }
    }
}

impl Drop for ConsumerHub {
    fn drop(&mut self) {
        self.shutdown();
    }
}
