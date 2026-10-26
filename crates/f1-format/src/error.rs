use std::fmt;
use std::io;

#[derive(Debug)]
pub enum FormatError {
    Io(io::Error),
    /// The file does not start with a supported magic number.
    NotARecording,
    /// Structurally invalid content at a byte offset.
    Corrupt {
        offset: u64,
        reason: String,
    },
    Json(serde_json::Error),
}

impl FormatError {
    pub(crate) fn corrupt(offset: u64, reason: impl Into<String>) -> Self {
        FormatError::Corrupt { offset, reason: reason.into() }
    }
}

impl fmt::Display for FormatError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FormatError::Io(err) => write!(f, "{err}"),
            FormatError::NotARecording => f.write_str("not a recording or unsupported format version"),
            FormatError::Corrupt { offset, reason } => write!(f, "corrupt at byte {offset}: {reason}"),
            FormatError::Json(err) => write!(f, "invalid JSON: {err}"),
        }
    }
}

impl std::error::Error for FormatError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            FormatError::Io(err) => Some(err),
            FormatError::Json(err) => Some(err),
            _ => None,
        }
    }
}

impl From<io::Error> for FormatError {
    fn from(err: io::Error) -> Self {
        FormatError::Io(err)
    }
}

impl From<serde_json::Error> for FormatError {
    fn from(err: serde_json::Error) -> Self {
        FormatError::Json(err)
    }
}
