use std::fs::File;
use std::io::{self, BufReader, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

use crate::{
    BLOCK_HEADER_LEN, BLOCK_MAGIC, FILE_HEADER_LEN, FILE_MAGIC, FOOTER_HEADER_LEN, FOOTER_MAGIC, Footer, FormatError,
    Indexer, MAX_BLOCK_BODY_LEN, MAX_FOOTER_LEN, MAX_METADATA_LEN, Records, Summary, TRAILER_LEN, TRAILER_MAGIC,
    validate_body,
};

#[derive(Debug, Clone, PartialEq)]
pub struct FileHeader {
    /// Wall-clock time of the recording's time origin (record timestamp zero).
    pub start_unix_ns: u64,
    pub flags: u32,
    pub metadata: serde_json::Value,
    /// Offset of the first block.
    pub data_offset: u64,
}

/// How a recording ended, known once [`RecordingReader::next_block`] returns `None`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EndState {
    /// A valid footer and trailer follow the last block.
    Complete,
    /// The file ends exactly after a block, without a footer (writer stopped abruptly).
    Unclean,
    /// The file ends inside a block or footer; data from `offset` on is unreadable.
    Truncated { offset: u64 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BlockInfo {
    pub offset: u64,
    pub record_count: u32,
    pub first_ts_ns: u64,
}

/// Sequential, validating reader of a recording's blocks.
pub struct RecordingReader {
    inner: BufReader<File>,
    path: PathBuf,
    header: FileHeader,
    offset: u64,
    footer: Option<Footer>,
    end: Option<EndState>,
}

impl RecordingReader {
    pub fn open(path: &Path) -> Result<Self, FormatError> {
        let file = File::open(path)?;
        let mut inner = BufReader::with_capacity(1 << 20, file);
        let header = read_file_header(&mut inner)?;
        Ok(Self { inner, path: path.to_path_buf(), offset: header.data_offset, header, footer: None, end: None })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn header(&self) -> &FileHeader {
        &self.header
    }

    pub fn footer(&self) -> Option<&Footer> {
        self.footer.as_ref()
    }

    pub fn end_state(&self) -> Option<&EndState> {
        self.end.as_ref()
    }

    /// Reads the next block body into `body` (replacing its contents), after
    /// checking its CRC and record structure. Returns `None` at the end.
    pub fn next_block(&mut self, body: &mut Vec<u8>) -> Result<Option<BlockInfo>, FormatError> {
        if self.end.is_some() {
            return Ok(None);
        }
        let offset = self.offset;
        let mut magic = [0u8; 4];
        match read_full(&mut self.inner, &mut magic)? {
            0 => return self.finish(EndState::Unclean),
            4 => {}
            _ => return self.finish(EndState::Truncated { offset }),
        }
        if &magic == FOOTER_MAGIC {
            return self.read_footer_entry(offset);
        }
        if &magic != BLOCK_MAGIC {
            return Err(FormatError::corrupt(offset, "expected a block or footer"));
        }
        let mut rest = [0u8; BLOCK_HEADER_LEN - 4];
        if read_full(&mut self.inner, &mut rest)? != rest.len() {
            return self.finish(EndState::Truncated { offset });
        }
        let record_count = u32::from_le_bytes(rest[0..4].try_into().unwrap());
        let body_len = u32::from_le_bytes(rest[4..8].try_into().unwrap()) as usize;
        let crc = u32::from_le_bytes(rest[8..12].try_into().unwrap());
        let first_ts_ns = u64::from_le_bytes(rest[12..20].try_into().unwrap());
        if body_len > MAX_BLOCK_BODY_LEN {
            return Err(FormatError::corrupt(offset, "block length exceeds the safety limit"));
        }
        body.clear();
        body.resize(body_len, 0);
        if read_full(&mut self.inner, body)? != body_len {
            return self.finish(EndState::Truncated { offset });
        }
        if crc32fast::hash(body) != crc {
            return Err(FormatError::corrupt(offset, "block checksum mismatch"));
        }
        let counted = validate_body(body).map_err(|reason| FormatError::corrupt(offset, reason))?;
        if counted != record_count {
            return Err(FormatError::corrupt(offset, "block record count does not match its body"));
        }
        self.offset += (BLOCK_HEADER_LEN + body_len) as u64;
        Ok(Some(BlockInfo { offset, record_count, first_ts_ns }))
    }

    fn read_footer_entry(&mut self, offset: u64) -> Result<Option<BlockInfo>, FormatError> {
        let mut rest = [0u8; FOOTER_HEADER_LEN - 4];
        if read_full(&mut self.inner, &mut rest)? != rest.len() {
            return self.finish(EndState::Truncated { offset });
        }
        let len = u32::from_le_bytes(rest[0..4].try_into().unwrap()) as usize;
        let crc = u32::from_le_bytes(rest[4..8].try_into().unwrap());
        if len > MAX_FOOTER_LEN {
            return Err(FormatError::corrupt(offset, "footer length exceeds the safety limit"));
        }
        let mut json = vec![0u8; len];
        let mut trailer = [0u8; TRAILER_LEN];
        if read_full(&mut self.inner, &mut json)? != len || read_full(&mut self.inner, &mut trailer)? != TRAILER_LEN {
            return self.finish(EndState::Truncated { offset });
        }
        if crc32fast::hash(&json) != crc {
            return Err(FormatError::corrupt(offset, "footer checksum mismatch"));
        }
        if &trailer[8..] != TRAILER_MAGIC || u64::from_le_bytes(trailer[..8].try_into().unwrap()) != offset {
            return Err(FormatError::corrupt(offset, "trailer does not point at the footer"));
        }
        let mut extra = [0u8; 1];
        if read_full(&mut self.inner, &mut extra)? != 0 {
            return Err(FormatError::corrupt(offset, "data follows the trailer"));
        }
        self.footer = Some(serde_json::from_slice(&json)?);
        self.finish(EndState::Complete)
    }

    fn finish(&mut self, end: EndState) -> Result<Option<BlockInfo>, FormatError> {
        self.end = Some(end);
        Ok(None)
    }
}

fn read_file_header(inner: &mut impl Read) -> Result<FileHeader, FormatError> {
    let mut fixed = [0u8; FILE_HEADER_LEN];
    if read_full(inner, &mut fixed)? != FILE_HEADER_LEN || &fixed[..8] != FILE_MAGIC {
        return Err(FormatError::NotARecording);
    }
    let metadata_len = u32::from_le_bytes(fixed[8..12].try_into().unwrap()) as usize;
    let flags = u32::from_le_bytes(fixed[12..16].try_into().unwrap());
    let start_unix_ns = u64::from_le_bytes(fixed[16..24].try_into().unwrap());
    if metadata_len > MAX_METADATA_LEN {
        return Err(FormatError::corrupt(8, "metadata length exceeds the safety limit"));
    }
    let mut metadata = vec![0u8; metadata_len];
    if read_full(inner, &mut metadata)? != metadata_len {
        return Err(FormatError::corrupt(FILE_HEADER_LEN as u64, "file ends inside the metadata"));
    }
    Ok(FileHeader {
        start_unix_ns,
        flags,
        metadata: serde_json::from_slice(&metadata)?,
        data_offset: (FILE_HEADER_LEN + metadata_len) as u64,
    })
}

/// Reads the header and, via the trailer, the footer without scanning blocks.
/// Returns `None` for the footer if the recording was not closed cleanly.
pub fn read_footer(path: &Path) -> Result<(FileHeader, Option<Footer>), FormatError> {
    let mut file = File::open(path)?;
    let header = read_file_header(&mut BufReader::new(&mut file))?;
    let len = file.seek(SeekFrom::End(0))?;
    if len < header.data_offset + (FOOTER_HEADER_LEN + TRAILER_LEN) as u64 {
        return Ok((header, None));
    }
    let mut trailer = [0u8; TRAILER_LEN];
    file.seek(SeekFrom::End(-(TRAILER_LEN as i64)))?;
    file.read_exact(&mut trailer)?;
    if &trailer[8..] != TRAILER_MAGIC {
        return Ok((header, None));
    }
    let footer_offset = u64::from_le_bytes(trailer[..8].try_into().unwrap());
    if footer_offset < header.data_offset || footer_offset > len - (FOOTER_HEADER_LEN + TRAILER_LEN) as u64 {
        return Err(FormatError::corrupt(len - TRAILER_LEN as u64, "trailer points outside the file"));
    }
    file.seek(SeekFrom::Start(footer_offset))?;
    let mut fixed = [0u8; FOOTER_HEADER_LEN];
    file.read_exact(&mut fixed)?;
    let json_len = u32::from_le_bytes(fixed[4..8].try_into().unwrap()) as u64;
    if &fixed[..4] != FOOTER_MAGIC || footer_offset + FOOTER_HEADER_LEN as u64 + json_len + TRAILER_LEN as u64 != len {
        return Err(FormatError::corrupt(footer_offset, "trailer does not point at a footer"));
    }
    let mut json = vec![0u8; json_len as usize];
    file.read_exact(&mut json)?;
    if crc32fast::hash(&json) != u32::from_le_bytes(fixed[8..12].try_into().unwrap()) {
        return Err(FormatError::corrupt(footer_offset, "footer checksum mismatch"));
    }
    Ok((header, Some(serde_json::from_slice(&json)?)))
}

/// Result of reading every block of a recording.
#[derive(Debug, Clone)]
pub struct ScanReport {
    pub header: FileHeader,
    pub end: EndState,
    pub blocks: u64,
    /// Summary rebuilt from the blocks that could be read.
    pub rebuilt: Summary,
    pub footer: Option<Footer>,
    /// Set if a block failed validation; blocks after it were not read.
    pub corruption: Option<String>,
}

/// Reads and validates every block, rebuilding the summary from scratch.
pub fn scan(path: &Path) -> Result<ScanReport, FormatError> {
    let mut reader = RecordingReader::open(path)?;
    let mut indexer = Indexer::new();
    let mut body = Vec::new();
    let mut blocks = 0u64;
    let mut corruption = None;
    loop {
        match reader.next_block(&mut body) {
            Ok(Some(_)) => {
                blocks += 1;
                for record in Records::new(&body) {
                    indexer.observe(&record);
                }
            }
            Ok(None) => break,
            Err(FormatError::Corrupt { offset, reason }) => {
                corruption = Some(format!("byte {offset}: {reason}"));
                break;
            }
            Err(err) => return Err(err),
        }
    }
    let end = reader.end_state().cloned().unwrap_or(EndState::Truncated { offset: reader.offset });
    Ok(ScanReport {
        header: reader.header.clone(),
        end,
        blocks,
        rebuilt: indexer.summary(),
        footer: reader.footer.clone(),
        corruption,
    })
}

/// Reads until `buf` is full or EOF, returning the number of bytes read.
fn read_full(reader: &mut impl Read, buf: &mut [u8]) -> io::Result<usize> {
    let mut filled = 0;
    while filled < buf.len() {
        match reader.read(&mut buf[filled..]) {
            Ok(0) => break,
            Ok(n) => filled += n,
            Err(err) if err.kind() == io::ErrorKind::Interrupted => {}
            Err(err) => return Err(err),
        }
    }
    Ok(filled)
}
