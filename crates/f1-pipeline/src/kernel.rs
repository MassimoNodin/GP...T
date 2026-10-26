use std::net::UdpSocket;

use serde::Serialize;

/// Operating-system counters for one UDP socket.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
pub struct KernelSample {
    /// Datagrams the kernel dropped because the socket buffer was full (cumulative).
    pub drops: u64,
    /// Bytes currently queued in the socket's receive buffer.
    pub rx_queue_bytes: u64,
}

/// Reads per-socket drop counters. Only Linux exposes them (via
/// `/proc/net/udp`); elsewhere kernel drops are reported as unknown, never zero.
pub struct KernelSampler {
    #[cfg_attr(not(target_os = "linux"), allow(dead_code))]
    inode: u64,
}

impl KernelSampler {
    #[cfg(target_os = "linux")]
    pub fn for_socket(socket: &UdpSocket) -> Option<Self> {
        use std::os::fd::AsRawFd;
        let link = std::fs::read_link(format!("/proc/self/fd/{}", socket.as_raw_fd())).ok()?;
        let text = link.to_str()?;
        let inode = text.strip_prefix("socket:[")?.strip_suffix(']')?.parse().ok()?;
        let sampler = Self { inode };
        sampler.sample().map(|_| sampler)
    }

    #[cfg(not(target_os = "linux"))]
    pub fn for_socket(_socket: &UdpSocket) -> Option<Self> {
        None
    }

    #[cfg(target_os = "linux")]
    pub fn sample(&self) -> Option<KernelSample> {
        ["/proc/net/udp", "/proc/net/udp6"].iter().find_map(|path| {
            let table = std::fs::read_to_string(path).ok()?;
            parse_proc_udp(&table, self.inode)
        })
    }

    #[cfg(not(target_os = "linux"))]
    pub fn sample(&self) -> Option<KernelSample> {
        None
    }
}

/// Finds the socket with `inode` in a `/proc/net/udp` table.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn parse_proc_udp(table: &str, inode: u64) -> Option<KernelSample> {
    table.lines().skip(1).find_map(|line| {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() < 13 || fields[9].parse::<u64>().ok()? != inode {
            return None;
        }
        let rx_queue = fields[4].split(':').nth(1)?;
        Some(KernelSample { drops: fields[12].parse().ok()?, rx_queue_bytes: u64::from_str_radix(rx_queue, 16).ok()? })
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn parses_proc_net_udp() {
        let table = "\
   sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops
  123: 00000000:5129 00000000:0000 07 00000000:00000A00 00:00000000 00000000  1000        0 98765 2 0000000000000000 17
  124: 00000000:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000     0        0 11111 2 0000000000000000 0
";
        let sample = super::parse_proc_udp(table, 98765).unwrap();
        assert_eq!(sample.drops, 17);
        assert_eq!(sample.rx_queue_bytes, 0xA00);
        assert!(super::parse_proc_udp(table, 5).is_none());
    }
}
