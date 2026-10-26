use crate::FormatError;

/// Record header: timestamp `u64`, payload length `u16`, kind `u8`, reserved `u8`.
pub const RECORD_HEADER_LEN: usize = 12;

/// What a record's payload holds. Unknown kinds are preserved and skipped.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RecordKind(pub u8);

impl RecordKind {
    /// A UDP datagram exactly as received.
    pub const DATAGRAM: RecordKind = RecordKind(0);
    /// A pipeline control datagram (stress-test markers), prefixed by its source address.
    pub const CONTROL: RecordKind = RecordKind(1);
    /// Counts of datagrams the receiver knows it lost just before this point.
    pub const LOSS: RecordKind = RecordKind(2);
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Record<'a> {
    /// Receive time in nanoseconds since the recording's time origin.
    pub ts_ns: u64,
    pub kind: RecordKind,
    pub payload: &'a [u8],
}

/// Appends one encoded record. Payloads longer than `u16::MAX` are not
/// representable; UDP datagrams over IPv4/IPv6 without jumbograms always fit.
pub fn encode_record(out: &mut Vec<u8>, ts_ns: u64, kind: RecordKind, payload: &[u8]) {
    assert!(payload.len() <= u16::MAX as usize, "record payload too large");
    out.extend_from_slice(&ts_ns.to_le_bytes());
    out.extend_from_slice(&(payload.len() as u16).to_le_bytes());
    out.push(kind.0);
    out.push(0);
    out.extend_from_slice(payload);
}

/// Iterates the records of a block body. Stops at the first malformed record;
/// use [`validate_body`] first when the body comes from untrusted storage.
#[derive(Debug, Clone)]
pub struct Records<'a> {
    body: &'a [u8],
    pos: usize,
}

impl<'a> Records<'a> {
    pub fn new(body: &'a [u8]) -> Self {
        Self { body, pos: 0 }
    }

    /// Byte offset of the next record within the body.
    pub fn position(&self) -> usize {
        self.pos
    }
}

impl<'a> Iterator for Records<'a> {
    type Item = Record<'a>;

    fn next(&mut self) -> Option<Record<'a>> {
        let rest = self.body.get(self.pos..)?;
        if rest.len() < RECORD_HEADER_LEN {
            return None;
        }
        let ts_ns = u64::from_le_bytes(rest[0..8].try_into().unwrap());
        let len = u16::from_le_bytes([rest[8], rest[9]]) as usize;
        let kind = RecordKind(rest[10]);
        let payload = rest.get(RECORD_HEADER_LEN..RECORD_HEADER_LEN + len)?;
        self.pos += RECORD_HEADER_LEN + len;
        Some(Record { ts_ns, kind, payload })
    }
}

/// Checks that a body is an exact sequence of records and returns their count.
pub fn validate_body(body: &[u8]) -> Result<u32, String> {
    let mut pos = 0usize;
    let mut count = 0u32;
    while pos < body.len() {
        let rest = &body[pos..];
        if rest.len() < RECORD_HEADER_LEN {
            return Err(format!("partial record header at body offset {pos}"));
        }
        let len = u16::from_le_bytes([rest[8], rest[9]]) as usize;
        if rest.len() < RECORD_HEADER_LEN + len {
            return Err(format!("record at body offset {pos} overruns the block"));
        }
        pos += RECORD_HEADER_LEN + len;
        count += 1;
    }
    Ok(count)
}

/// Payload of a [`RecordKind::LOSS`] record.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct LossCounts {
    /// Datagrams received but discarded because the pipeline was saturated.
    pub app_dropped: u32,
    /// Datagrams the operating system reported dropping on the socket.
    pub kernel_dropped: u32,
}

impl LossCounts {
    pub const LEN: usize = 8;

    pub fn encode(&self) -> [u8; Self::LEN] {
        let mut out = [0u8; Self::LEN];
        out[0..4].copy_from_slice(&self.app_dropped.to_le_bytes());
        out[4..8].copy_from_slice(&self.kernel_dropped.to_le_bytes());
        out
    }

    pub fn decode(payload: &[u8]) -> Result<Self, FormatError> {
        let bytes: &[u8; Self::LEN] =
            payload.try_into().map_err(|_| FormatError::corrupt(0, "loss record has the wrong length"))?;
        Ok(Self {
            app_dropped: u32::from_le_bytes(bytes[0..4].try_into().unwrap()),
            kernel_dropped: u32::from_le_bytes(bytes[4..8].try_into().unwrap()),
        })
    }
}
