use f1_format::{RecordKind, Records, encode_record};

/// Records encoded in the on-disk record format, ready to become one block.
#[derive(Debug, Default)]
pub struct Batch {
    pub body: Vec<u8>,
    pub records: u32,
    pub datagrams: u32,
    pub first_ts_ns: u64,
}

impl Batch {
    pub fn with_capacity(bytes: usize) -> Self {
        Self { body: Vec::with_capacity(bytes), ..Self::default() }
    }

    pub fn push(&mut self, ts_ns: u64, kind: RecordKind, payload: &[u8]) {
        if self.records == 0 {
            self.first_ts_ns = ts_ns;
        }
        encode_record(&mut self.body, ts_ns, kind, payload);
        self.records += 1;
        if kind == RecordKind::DATAGRAM {
            self.datagrams += 1;
        }
    }

    /// Copies already-encoded records into a new batch.
    pub fn from_body(body: &[u8]) -> Self {
        let mut batch = Self { body: body.to_vec(), ..Self::default() };
        for record in Records::new(body) {
            if batch.records == 0 {
                batch.first_ts_ns = record.ts_ns;
            }
            batch.records += 1;
            if record.kind == RecordKind::DATAGRAM {
                batch.datagrams += 1;
            }
        }
        batch
    }

    pub fn is_empty(&self) -> bool {
        self.records == 0
    }

    pub fn len_bytes(&self) -> usize {
        self.body.len()
    }
}
