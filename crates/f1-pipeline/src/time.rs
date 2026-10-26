/// Formats Unix nanoseconds as a compact UTC stamp such as `20261026T143005Z`.
pub fn utc_stamp(unix_ns: u64) -> String {
    let secs = unix_ns / 1_000_000_000;
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}{month:02}{day:02}T{:02}{:02}{:02}Z", rem / 3600, (rem % 3600) / 60, rem % 60)
}

/// Howard Hinnant's days-to-civil conversion for the proleptic Gregorian calendar.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    #[test]
    fn formats_known_instants() {
        assert_eq!(super::utc_stamp(0), "19700101T000000Z");
        assert_eq!(super::utc_stamp(1_793_025_005_000_000_000), "20261026T143005Z");
        assert_eq!(super::utc_stamp(951_782_400_000_000_000), "20000229T000000Z");
    }
}
