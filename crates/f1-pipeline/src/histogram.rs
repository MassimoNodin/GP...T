use serde::{Deserialize, Serialize};

const SUB_BITS: u32 = 5;
const SUB: usize = 1 << SUB_BITS;
const BUCKETS: usize = (64 - SUB_BITS as usize + 1) * SUB;

/// Log-linear histogram of nanosecond values with at most ~3% relative
/// error, fixed size and no allocation after construction.
#[derive(Clone)]
pub struct Histogram {
    counts: Box<[u64; BUCKETS]>,
    count: u64,
    max: u64,
}

impl Histogram {
    pub fn new() -> Self {
        Self { counts: Box::new([0; BUCKETS]), count: 0, max: 0 }
    }

    pub fn record(&mut self, value: u64) {
        self.counts[bucket(value)] += 1;
        self.count += 1;
        self.max = self.max.max(value);
    }

    pub fn count(&self) -> u64 {
        self.count
    }

    pub fn max(&self) -> u64 {
        self.max
    }

    pub fn merge(&mut self, other: &Histogram) {
        for (mine, theirs) in self.counts.iter_mut().zip(other.counts.iter()) {
            *mine += theirs;
        }
        self.count += other.count;
        self.max = self.max.max(other.max);
    }

    pub fn reset(&mut self) {
        self.counts.fill(0);
        self.count = 0;
        self.max = 0;
    }

    /// Upper bound of the bucket holding quantile `q` (0..=1), capped at the maximum.
    pub fn quantile(&self, q: f64) -> u64 {
        if self.count == 0 {
            return 0;
        }
        let rank = ((q * self.count as f64).ceil() as u64).clamp(1, self.count);
        let mut seen = 0;
        for (index, &n) in self.counts.iter().enumerate() {
            seen += n;
            if seen >= rank {
                return upper_bound(index).min(self.max);
            }
        }
        self.max
    }

    pub fn summary(&self) -> LatencySummary {
        let ms = |ns: u64| (ns as f64 / 1e6 * 1000.0).round() / 1000.0;
        LatencySummary {
            count: self.count,
            p50_ms: ms(self.quantile(0.50)),
            p99_ms: ms(self.quantile(0.99)),
            p999_ms: ms(self.quantile(0.999)),
            max_ms: ms(self.max),
        }
    }
}

impl Default for Histogram {
    fn default() -> Self {
        Self::new()
    }
}

fn bucket(value: u64) -> usize {
    if value < SUB as u64 {
        return value as usize;
    }
    let exponent = 63 - value.leading_zeros();
    let mantissa = (value >> (exponent - SUB_BITS)) as usize & (SUB - 1);
    (exponent - SUB_BITS + 1) as usize * SUB + mantissa
}

fn upper_bound(index: usize) -> u64 {
    if index < SUB {
        return index as u64;
    }
    let shift = (index / SUB) as u32 - 1;
    let mantissa = (index % SUB) as u64;
    ((SUB as u64 + mantissa) << shift) + ((1u64 << shift) - 1)
}

/// Latency percentiles in milliseconds.
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct LatencySummary {
    pub count: u64,
    pub p50_ms: f64,
    pub p99_ms: f64,
    pub p999_ms: f64,
    pub max_ms: f64,
}

/// The same measurements kept over three scopes: the whole process, the
/// current stats interval and the current stress-test run.
#[derive(Clone, Default)]
pub(crate) struct LatencySet {
    pub total: Histogram,
    pub interval: Histogram,
    pub run: Histogram,
}

impl LatencySet {
    pub fn record(&mut self, value: u64) {
        self.total.record(value);
        self.interval.record(value);
        self.run.record(value);
    }

    pub fn take_interval(&mut self) -> LatencySummary {
        let summary = self.interval.summary();
        self.interval.reset();
        summary
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn buckets_are_monotonic_and_bounded() {
        let mut last = 0;
        for value in (0..2_000_000u64).step_by(7).chain([u64::MAX / 2, u64::MAX]) {
            let index = bucket(value);
            assert!(index < BUCKETS);
            assert!(index >= last);
            last = index;
            let upper = upper_bound(index);
            assert!(upper >= value, "{value} above bucket bound {upper}");
            assert!(value < 32 || (upper - value) as f64 / value as f64 <= 1.0 / 32.0);
        }
    }

    #[test]
    fn quantiles_are_close() {
        let mut h = Histogram::new();
        for value in 1..=10_000u64 {
            h.record(value * 1_000);
        }
        let p50 = h.quantile(0.5) as f64;
        assert!((p50 - 5_000_000.0).abs() / 5_000_000.0 < 0.04);
        assert_eq!(h.quantile(1.0), 10_000_000);
        assert_eq!(h.count(), 10_000);
        let mut other = Histogram::new();
        other.record(20_000_000);
        h.merge(&other);
        assert_eq!(h.max(), 20_000_000);
    }
}
