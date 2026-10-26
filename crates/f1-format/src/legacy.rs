//! Reader for the retired Python `F1ECAP01` capture format, used only to
//! convert existing recordings into `.f1r`.
//!
//! Layout (big-endian): magic `F1ECAP01`, `u32` metadata length, metadata
//! JSON, then entries. Entry type 1 is a datagram: `u64` sequence, `u64`
//! wall-clock ns, `u64` monotonic ns, `u16` host length, `u16` port, `u32`
//! payload length, host bytes, payload. Entry type 2 is the footer: `u32`
//! length and JSON, and must be last.

use std::fs::File;
use std::io::{self, BufReader, Read};
use std::path::Path;

use crate::FormatError;

const MAGIC: &[u8; 8] = b"F1ECAP01";
const MAX_METADATA: usize = 1 << 20;
const MAX_HOST: usize = 512;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LegacyDatagram {
    pub sequence: u64,
    pub captured_at_ns: u64,
    pub monotonic_ns: u64,
    pub source_host: String,
    pub source_port: u16,
    pub payload: Vec<u8>,
}

pub struct LegacyCapture {
    inner: BufReader<File>,
    offset: u64,
    pub metadata: serde_json::Value,
    /// The completion footer, once reached.
    pub footer: Option<serde_json::Value>,
    /// Set if the file ended inside an entry (as in recovered captures).
    pub truncated: bool,
}

impl LegacyCapture {
    pub fn open(path: &Path) -> Result<Self, FormatError> {
        let mut inner = BufReader::with_capacity(1 << 20, File::open(path)?);
        let mut magic = [0u8; 8];
        if inner.read_exact(&mut magic).is_err() || &magic != MAGIC {
            return Err(FormatError::NotARecording);
        }
        let len = read_u32(&mut inner)? as usize;
        if len > MAX_METADATA {
            return Err(FormatError::corrupt(8, "capture metadata exceeds the safety limit"));
        }
        let mut metadata = vec![0u8; len];
        inner.read_exact(&mut metadata)?;
        Ok(Self {
            inner,
            offset: 12 + len as u64,
            metadata: serde_json::from_slice(&metadata)?,
            footer: None,
            truncated: false,
        })
    }

    pub fn next_datagram(&mut self) -> Result<Option<LegacyDatagram>, FormatError> {
        if self.footer.is_some() || self.truncated {
            return Ok(None);
        }
        let mut entry = [0u8; 1];
        if self.inner.read(&mut entry)? == 0 {
            return Ok(None);
        }
        match entry[0] {
            1 => match self.read_datagram() {
                Ok(datagram) => Ok(Some(datagram)),
                Err(FormatError::Io(err)) if err.kind() == io::ErrorKind::UnexpectedEof => {
                    self.truncated = true;
                    Ok(None)
                }
                Err(err) => Err(err),
            },
            2 => {
                let len = read_u32(&mut self.inner)? as usize;
                if len > MAX_METADATA {
                    return Err(FormatError::corrupt(self.offset, "capture footer exceeds the safety limit"));
                }
                let mut json = vec![0u8; len];
                self.inner.read_exact(&mut json)?;
                self.footer = Some(serde_json::from_slice(&json)?);
                Ok(None)
            }
            other => Err(FormatError::corrupt(self.offset, format!("unknown capture entry type {other}"))),
        }
    }

    fn read_datagram(&mut self) -> Result<LegacyDatagram, FormatError> {
        let mut fixed = [0u8; 32];
        self.inner.read_exact(&mut fixed)?;
        let host_len = u16::from_be_bytes([fixed[24], fixed[25]]) as usize;
        let payload_len = u32::from_be_bytes(fixed[28..32].try_into().unwrap()) as usize;
        if host_len > MAX_HOST || payload_len > u16::MAX as usize {
            return Err(FormatError::corrupt(self.offset, "capture record exceeds a field size limit"));
        }
        let mut host = vec![0u8; host_len];
        self.inner.read_exact(&mut host)?;
        let mut payload = vec![0u8; payload_len];
        self.inner.read_exact(&mut payload)?;
        self.offset += 1 + 32 + (host_len + payload_len) as u64;
        Ok(LegacyDatagram {
            sequence: u64::from_be_bytes(fixed[0..8].try_into().unwrap()),
            captured_at_ns: u64::from_be_bytes(fixed[8..16].try_into().unwrap()),
            monotonic_ns: u64::from_be_bytes(fixed[16..24].try_into().unwrap()),
            source_host: String::from_utf8_lossy(&host).into_owned(),
            source_port: u16::from_be_bytes([fixed[26], fixed[27]]),
            payload,
        })
    }
}

fn read_u32(reader: &mut impl Read) -> io::Result<u32> {
    let mut bytes = [0u8; 4];
    reader.read_exact(&mut bytes)?;
    Ok(u32::from_be_bytes(bytes))
}
