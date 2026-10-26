use std::path::Path;
use std::process::ExitCode;

use f1_format::legacy::LegacyCapture;
use f1_format::{RecordKind, RecordingWriter};
use serde_json::json;

use crate::inspect::print_summary;
use crate::util::Result;

/// Rewrites an F1ECAP01 capture as `.f1r`, keeping every payload byte-for-byte
/// and the original monotonic receive spacing.
pub fn run(input: &Path, output: &Path, label: Option<String>) -> Result<ExitCode> {
    let mut capture = LegacyCapture::open(input)?;
    let Some(first) = capture.next_datagram()? else {
        return Err(format!("{} contains no datagrams", input.display()).into());
    };
    let origin_mono = first.monotonic_ns;
    let metadata = json!({
        "tool": "f1-rec convert",
        "version": env!("CARGO_PKG_VERSION"),
        "label": label,
        "source_format": "F1ECAP01",
        "source_file": input.file_name().map(|name| name.to_string_lossy().into_owned()),
        "source_metadata": capture.metadata,
        "source_host": first.source_host,
    });
    let mut writer = RecordingWriter::create(output, first.captured_at_ns, &metadata)?;
    writer.push(0, RecordKind::DATAGRAM, &first.payload)?;

    let mut last_ts = 0u64;
    let mut non_monotonic = 0u64;
    let mut converted = 1u64;
    while let Some(datagram) = capture.next_datagram()? {
        let mut ts = datagram.monotonic_ns.saturating_sub(origin_mono);
        if ts < last_ts {
            non_monotonic += 1;
            ts = last_ts;
        }
        last_ts = ts;
        writer.push(ts, RecordKind::DATAGRAM, &datagram.payload)?;
        converted += 1;
    }
    let footer = writer.finish(json!({
        "closed_reason": "converted",
        "source_footer": capture.footer,
        "source_truncated": capture.truncated,
        "non_monotonic_timestamps": non_monotonic,
    }))?;
    eprintln!(
        "converted {converted} datagrams{}{}",
        if capture.truncated { " (source ended mid-record; the partial record was skipped)" } else { "" },
        if non_monotonic > 0 { format!("; {non_monotonic} out-of-order timestamps clamped") } else { String::new() },
    );
    print_summary(Some(output), &footer.summary);
    Ok(ExitCode::SUCCESS)
}
