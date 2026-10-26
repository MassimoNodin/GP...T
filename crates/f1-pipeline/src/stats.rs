use std::sync::atomic::{AtomicU64, Ordering};

use serde::Serialize;

use crate::control::Verdict;
use crate::{ConsumerReport, LatencySummary};

/// Counters shared between the receiver and writer threads.
#[derive(Debug, Default)]
pub struct Metrics {
    pub received: AtomicU64,
    pub received_bytes: AtomicU64,
    pub control_received: AtomicU64,
    /// Datagrams received but discarded because the pipeline was saturated.
    pub app_dropped: AtomicU64,
    pub recv_errors: AtomicU64,
    pub forwarded: AtomicU64,
    pub forward_errors: AtomicU64,
    pub written_records: AtomicU64,
    pub write_errors: AtomicU64,
    /// Datagrams that reached the writer but could not be stored.
    pub lost_on_write: AtomicU64,
}

impl Metrics {
    pub(crate) fn get(counter: &AtomicU64) -> u64 {
        counter.load(Ordering::Relaxed)
    }

    pub(crate) fn add(counter: &AtomicU64, value: u64) {
        counter.fetch_add(value, Ordering::Relaxed);
    }
}

/// One line of periodic pipeline statistics.
#[derive(Debug, Clone, Default, Serialize)]
pub struct StatsSnapshot {
    pub uptime_s: f64,
    pub interval_s: f64,
    pub received: u64,
    pub received_per_s: f64,
    pub received_bytes: u64,
    pub app_dropped: u64,
    /// Cumulative socket drops since start; `None` where not measurable.
    pub kernel_dropped: Option<u64>,
    pub kernel_rx_queue_peak_bytes: Option<u64>,
    pub recv_buffer_bytes: usize,
    pub recv_errors: u64,
    pub forwarded: u64,
    pub forward_errors: u64,
    pub written_records: u64,
    pub write_errors: u64,
    pub lost_on_write: u64,
    /// Largest batch the writer took: how much had queued up while it was busy.
    pub intake_peak_bytes: u64,
    pub written_latency: LatencySummary,
    pub durable_latency: LatencySummary,
    pub total_written_latency: LatencySummary,
    pub total_durable_latency: LatencySummary,
    pub segment: Option<String>,
    pub test_run: Option<String>,
    pub consumers: Vec<ConsumerReport>,
}

/// Something the pipeline reports while running.
#[derive(Debug, Clone, Copy)]
pub enum PipelineEvent<'a> {
    Stats(&'a StatsSnapshot),
    RunStarted { run_id: &'a str, archive: &'a std::path::Path },
    RunFinished(&'a Verdict),
}
