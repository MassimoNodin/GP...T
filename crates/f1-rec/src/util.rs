use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

/// A file, or the `.f1r` files directly inside a directory in name order
/// (archive segments are named so that this is recording order).
pub fn recording_files(path: &Path) -> io::Result<Vec<PathBuf>> {
    if !path.is_dir() {
        return Ok(vec![path.to_path_buf()]);
    }
    let mut files: Vec<PathBuf> = fs::read_dir(path)?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| path.extension().is_some_and(|ext| ext == f1_format::EXTENSION))
        .collect();
    files.sort();
    if files.is_empty() {
        return Err(io::Error::new(io::ErrorKind::NotFound, format!("no .f1r files in {}", path.display())));
    }
    Ok(files)
}

pub fn format_duration_ns(ns: u64) -> String {
    let total = ns as f64 / 1e9;
    let hours = (total / 3600.0).floor();
    let minutes = ((total - hours * 3600.0) / 60.0).floor();
    let seconds = total - hours * 3600.0 - minutes * 60.0;
    if hours > 0.0 {
        format!("{hours:.0}h{minutes:02.0}m{seconds:04.1}s")
    } else if minutes > 0.0 {
        format!("{minutes:.0}m{seconds:04.1}s")
    } else {
        format!("{seconds:.1}s")
    }
}

pub fn format_bytes(bytes: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KiB", "MiB", "GiB", "TiB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit < UNITS.len() - 1 {
        value /= 1024.0;
        unit += 1;
    }
    if unit == 0 { format!("{bytes} B") } else { format!("{value:.1} {}", UNITS[unit]) }
}

/// Flag set by Ctrl+C (or SIGTERM). Installed once per process.
pub fn interrupt_flag() -> Arc<AtomicBool> {
    let flag = Arc::new(AtomicBool::new(false));
    let handler = Arc::clone(&flag);
    if let Err(err) = ctrlc::set_handler(move || handler.store(true, Ordering::Relaxed)) {
        eprintln!("warning: Ctrl+C handling unavailable: {err}");
    }
    flag
}

pub fn file_stem(path: &Path) -> String {
    path.file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or_else(|| "recording".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_durations_and_sizes() {
        assert_eq!(format_duration_ns(1_500_000_000), "1.5s");
        assert_eq!(format_duration_ns(264_526_000_000), "4m24.5s");
        assert_eq!(format_duration_ns(3_725_000_000_000), "1h02m05.0s");
        assert_eq!(format_bytes(512), "512 B");
        assert_eq!(format_bytes(92_040_422), "87.8 MiB");
    }
}
