use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use crate::{
    BLOCK_HEADER_LEN, BLOCK_MAGIC, FILE_MAGIC, FOOTER_MAGIC, Footer, Indexer, MAX_BLOCK_BODY_LEN, MAX_METADATA_LEN,
    RecordKind, Records, TRAILER_MAGIC, encode_record, unix_now_ns,
};

/// Default size at which [`RecordingWriter::push`] closes a block.
const DEFAULT_BLOCK_TARGET: usize = 256 * 1024;

/// Appends blocks to a new recording file and writes the footer on
/// [`finish`](RecordingWriter::finish). A recording without a footer is still
/// readable up to its last complete block.
pub struct RecordingWriter {
    file: File,
    path: PathBuf,
    len: u64,
    indexer: Indexer,
    /// Block under construction for `push`: header space followed by records.
    pending: Vec<u8>,
    pending_count: u32,
    pending_first_ts: u64,
    block_target: usize,
    failed: bool,
}

impl RecordingWriter {
    /// Creates `path`, failing if it already exists.
    pub fn create(path: &Path, start_unix_ns: u64, metadata: &serde_json::Value) -> io::Result<Self> {
        let metadata = serde_json::to_vec(metadata)?;
        if metadata.len() > MAX_METADATA_LEN {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "recording metadata is too large"));
        }
        let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
        let mut header = Vec::with_capacity(32 + metadata.len());
        header.extend_from_slice(FILE_MAGIC);
        header.extend_from_slice(&(metadata.len() as u32).to_le_bytes());
        header.extend_from_slice(&0u32.to_le_bytes());
        header.extend_from_slice(&start_unix_ns.to_le_bytes());
        header.extend_from_slice(&0u64.to_le_bytes());
        header.extend_from_slice(&metadata);
        file.write_all(&header)?;
        let mut pending = Vec::with_capacity(DEFAULT_BLOCK_TARGET + 2048);
        pending.resize(BLOCK_HEADER_LEN, 0);
        Ok(Self {
            file,
            path: path.to_path_buf(),
            len: header.len() as u64,
            indexer: Indexer::new(),
            pending,
            pending_count: 0,
            pending_first_ts: 0,
            block_target: DEFAULT_BLOCK_TARGET,
            failed: false,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Bytes written to the file so far, excluding records still buffered by `push`.
    pub fn len(&self) -> u64 {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.indexer.records() == 0 && self.pending_count == 0
    }

    pub fn indexer(&self) -> &Indexer {
        &self.indexer
    }

    /// Buffers one record, writing a block whenever the buffer reaches its target size.
    pub fn push(&mut self, ts_ns: u64, kind: RecordKind, payload: &[u8]) -> io::Result<()> {
        if self.pending_count == 0 {
            self.pending_first_ts = ts_ns;
        }
        encode_record(&mut self.pending, ts_ns, kind, payload);
        self.pending_count += 1;
        if self.pending.len() - BLOCK_HEADER_LEN >= self.block_target {
            self.flush_pending()?;
        }
        Ok(())
    }

    /// Writes any records buffered by `push` as a block.
    pub fn flush_pending(&mut self) -> io::Result<()> {
        if self.pending_count == 0 {
            return Ok(());
        }
        self.check()?;
        let body_len = self.pending.len() - BLOCK_HEADER_LEN;
        let header = index_block(&mut self.indexer, &self.pending[BLOCK_HEADER_LEN..], self.pending_first_ts);
        self.pending[..BLOCK_HEADER_LEN].copy_from_slice(&header);
        let result = self.file.write_all(&self.pending);
        self.after_write(result, BLOCK_HEADER_LEN + body_len)?;
        self.pending.truncate(BLOCK_HEADER_LEN);
        self.pending_count = 0;
        Ok(())
    }

    /// Writes `body`, a sequence of encoded records, as one block.
    pub fn write_block(&mut self, body: &[u8]) -> io::Result<()> {
        if body.is_empty() {
            return Ok(());
        }
        if body.len() > MAX_BLOCK_BODY_LEN {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "block body is too large"));
        }
        self.flush_pending()?;
        self.check()?;
        let first_ts = Records::new(body).next().map_or(0, |record| record.ts_ns);
        let header = index_block(&mut self.indexer, body, first_ts);
        let mut result = self.file.write_all(&header);
        if result.is_ok() {
            result = self.file.write_all(body);
        }
        self.after_write(result, BLOCK_HEADER_LEN + body.len())
    }

    /// Writes buffered records and asks the OS to make the file durable.
    pub fn sync(&mut self) -> io::Result<()> {
        self.flush_pending()?;
        self.check()?;
        let result = self.file.sync_data();
        self.after_write(result, 0)
    }

    /// Writes the footer and trailer, makes the file durable and closes it.
    pub fn finish(mut self, extra: serde_json::Value) -> io::Result<Footer> {
        self.flush_pending()?;
        self.check()?;
        let footer = Footer { summary: self.indexer.summary(), closed_unix_ns: unix_now_ns(), extra };
        let json = serde_json::to_vec(&footer)?;
        let footer_offset = self.len;
        let mut bytes = Vec::with_capacity(json.len() + 32);
        bytes.extend_from_slice(FOOTER_MAGIC);
        bytes.extend_from_slice(&(json.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&crc32fast::hash(&json).to_le_bytes());
        bytes.extend_from_slice(&0u32.to_le_bytes());
        bytes.extend_from_slice(&json);
        bytes.extend_from_slice(&footer_offset.to_le_bytes());
        bytes.extend_from_slice(TRAILER_MAGIC);
        self.file.write_all(&bytes)?;
        self.file.sync_all()?;
        Ok(footer)
    }

    fn check(&self) -> io::Result<()> {
        if self.failed {
            Err(io::Error::other("recording writer failed earlier; the file ends at its last complete block"))
        } else {
            Ok(())
        }
    }

    fn after_write(&mut self, result: io::Result<()>, written: usize) -> io::Result<()> {
        match result {
            Ok(()) => {
                self.len += written as u64;
                Ok(())
            }
            Err(err) => {
                self.failed = true;
                Err(err)
            }
        }
    }
}

/// Indexes a block body's records and returns the block header for it.
fn index_block(indexer: &mut Indexer, body: &[u8], first_ts: u64) -> [u8; BLOCK_HEADER_LEN] {
    let mut count = 0u32;
    for record in Records::new(body) {
        indexer.observe(&record);
        count += 1;
    }
    block_header(count, body, first_ts)
}

fn block_header(count: u32, body: &[u8], first_ts: u64) -> [u8; BLOCK_HEADER_LEN] {
    let mut header = [0u8; BLOCK_HEADER_LEN];
    header[0..4].copy_from_slice(BLOCK_MAGIC);
    header[4..8].copy_from_slice(&count.to_le_bytes());
    header[8..12].copy_from_slice(&(body.len() as u32).to_le_bytes());
    header[12..16].copy_from_slice(&crc32fast::hash(body).to_le_bytes());
    header[16..24].copy_from_slice(&first_ts.to_le_bytes());
    header
}
