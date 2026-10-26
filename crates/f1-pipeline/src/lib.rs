//! Lossless UDP capture pipeline shared by `f1-ingest` and `f1-rec record`.
//!
//! ```text
//! socket ─▶ receiver thread ─(bounded batch queue)─▶ writer thread ─▶ .f1r segments
//!            timestamps, batches,                     indexes headers,
//!            optional forwarding                      fsyncs, test runs
//!                                                         │ Arc<Batch>, try_send
//!                                                         ▼
//!                                                consumer threads (decoders, features)
//! ```
//!
//! The receiver never blocks on anything downstream: if the queue is full it
//! grows its current batch up to a bound and only then drops, counting every
//! loss. Consumers receive batches after they are written and can never slow
//! the archive; a consumer that falls behind misses batches and reports it.

mod batch;
mod clock;
pub mod consumer;
pub mod control;
mod histogram;
mod kernel;
mod pipeline;
mod receiver;
mod stats;
mod time;
mod writer;

pub use batch::Batch;
pub use clock::Clock;
pub use consumer::{Consumer, ConsumerReport, SyntheticLoad};
pub use histogram::{Histogram, LatencySummary};
pub use kernel::{KernelSample, KernelSampler};
pub use pipeline::{Pipeline, PipelineConfig};
pub use receiver::ReceiverConfig;
pub use stats::{Metrics, PipelineEvent, StatsSnapshot};
pub use time::utc_stamp;
pub use writer::{SinkConfig, WriterConfig, WriterOutcome};

#[cfg(test)]
mod tests;
