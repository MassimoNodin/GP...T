use std::time::Instant;

/// Monotonic nanosecond clock anchored to a wall-clock origin. Record
/// timestamps are `now_ns()` values, so they never jump with clock changes.
#[derive(Debug, Clone, Copy)]
pub struct Clock {
    origin: Instant,
    origin_unix_ns: u64,
}

impl Clock {
    pub fn new() -> Self {
        Self { origin: Instant::now(), origin_unix_ns: f1_format::unix_now_ns() }
    }

    pub fn now_ns(&self) -> u64 {
        self.origin.elapsed().as_nanos() as u64
    }

    pub fn origin_unix_ns(&self) -> u64 {
        self.origin_unix_ns
    }
}

impl Default for Clock {
    fn default() -> Self {
        Self::new()
    }
}
