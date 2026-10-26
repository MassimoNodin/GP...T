//! The `.f1r` recording format shared by the recorder, the ingest archive and
//! the replay tools. See `docs/recording-format.md` for the byte layout.
//!
//! A recording is a file header, a sequence of CRC-protected blocks of
//! timestamped records, and (after a clean close) a JSON footer with a summary
//! of everything written plus a fixed trailer pointing at that footer.

mod error;
pub mod legacy;
mod reader;
mod record;
mod summary;
mod writer;

pub use error::FormatError;
pub use reader::{BlockInfo, EndState, FileHeader, RecordingReader, ScanReport, read_footer, scan};
pub use record::{LossCounts, RECORD_HEADER_LEN, Record, RecordKind, Records, encode_record, validate_body};
pub use summary::{Digest, Footer, Indexer, Rejected, SessionSummary, Summary};
pub use writer::RecordingWriter;

/// File extension used for recordings and archive segments.
pub const EXTENSION: &str = "f1r";

pub(crate) const FILE_MAGIC: &[u8; 8] = b"F1RECv01";
pub(crate) const FILE_HEADER_LEN: usize = 32;
pub(crate) const BLOCK_MAGIC: &[u8; 4] = b"BLK1";
pub(crate) const BLOCK_HEADER_LEN: usize = 24;
pub(crate) const FOOTER_MAGIC: &[u8; 4] = b"FTR1";
pub(crate) const FOOTER_HEADER_LEN: usize = 16;
pub(crate) const TRAILER_MAGIC: &[u8; 8] = b"F1RECEND";
pub(crate) const TRAILER_LEN: usize = 16;
pub(crate) const MAX_METADATA_LEN: usize = 1 << 20;
pub(crate) const MAX_BLOCK_BODY_LEN: usize = 64 << 20;
pub(crate) const MAX_FOOTER_LEN: usize = 64 << 20;

/// Current wall-clock time as nanoseconds since the Unix epoch.
pub fn unix_now_ns() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos() as u64).unwrap_or(0)
}

#[cfg(test)]
mod tests;
