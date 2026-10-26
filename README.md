# F1 Engineer

A telemetry pipeline for F1 25, rebuilt from the ground up in Rust. The first
stage only receives the game's UDP telemetry and archives every datagram,
while measuring that it really kept every one. Later stages (decoding,
features) are added only after they pass the
[feasibility rules](docs/feasibility-rules.md): a stress test replaying real
recorded sessions on the deployment host.

The previous Python/Next.js implementation is frozen in [`legacy/`](legacy/)
for reference.

## Layout

| Path | Purpose |
| --- | --- |
| `crates/f1-wire` | F1 25 (packet format 2025) header and packet catalogue. Body decoders go here. |
| `crates/f1-format` | The `.f1r` recording format ([spec](docs/recording-format.md)), its summary index, and a reader for legacy `.f1ecap` captures. |
| `crates/f1-pipeline` | Capture pipeline: receiver, writer, consumers, stress-test control protocol. |
| `crates/f1-ingest` | `f1-ingest`: the archiving service (runs on the Ubuntu host). |
| `crates/f1-rec` | `f1-rec`: record, replay, stress, convert, info, verify (runs anywhere; normally the Windows PC). |
| `corpus/` | Real-session recordings for stress tests ([catalog](corpus/README.md)); files are not committed. |
| `docs/` | [Feasibility rules](docs/feasibility-rules.md), [recording format](docs/recording-format.md). |

## How the pipeline works

```text
UDP socket ─▶ receiver thread ──(pending batch)──▶ writer thread ──▶ .f1r segments
              timestamps, forwards                 indexes headers,
                                                   fsyncs, test runs
                                                        │ after writing
                                                        ▼
                                              consumer threads (future decoders/features)
```

- **Receiver.** It blocks in `recv` and appends each datagram, timestamped from
  a monotonic clock, to a pending batch. It never waits on disk.
  - Receive timeouts are deliberately not used: on Windows a timed-out receive
    can discard a datagram that arrives at that moment.
  - If the writer stalls, the pending batch grows up to 32 MiB. Only beyond
    that are datagrams dropped, and every drop is counted and written into the
    archive as a loss marker.
- **Writer.** It takes the batch when it reaches 256 KiB or its oldest datagram
  reaches 10 ms, writes it as one CRC-protected block, and indexes packet
  headers (sessions, packet types, frames). It fsyncs every second.
  - It measures receive-to-written and receive-to-durable latency for every
    datagram.
  - On Linux it samples the socket's kernel drop counter. Other platforms
    report kernel drops as unknown, not zero.
- **Consumers.** Each consumer has its own thread and queue and receives
  batches only after they are written. A slow consumer misses batches (counted)
  instead of slowing the archive. This is where decoding and features will run.

## Build

Requires a Rust toolchain (`rustup`, stable 1.90 or later).

```sh
cargo build --release      # binaries in target/release/
cargo test
```

On the Ubuntu host, build from the same commit: `cargo build --release -p f1-ingest`.

## Use

### Archive live telemetry (Ubuntu)

```sh
f1-ingest --listen 0.0.0.0:20777 --archive /mnt/nvme/f1-engineer/archive --test-control
```

- It writes one JSON line per event to stdout: `started`, `stats` every 5 s,
  `test_run_started`, `test_run_finished` and `stopped`.
- Ctrl+C or SIGTERM drains everything received and closes the open segment
  with a footer.
- `--test-control` lets `f1-rec stress` run tests on the same port. Test data
  goes to `<archive>/tests/`, never into `live/`.
- Only one process can own the port. The legacy `f1-engineer` user service on
  the Ubuntu host also uses 20777. It is currently stopped, but still enabled,
  so it will reclaim the port after a reboot unless it is disabled
  (`systemctl --user disable f1-engineer`).
- The UFW rule for 20777/udp must name the Windows PC's current address.

### Track lap times for all cars

Run `f1-ingest` with `--track-lap-times` to enable a separate consumer for
F1 25 Lap Data v1 packets. It emits JSON `lap_time_completed` events with the
session UID, car index, completed lap number and reported time in milliseconds.
A `lap_session_started` event marks a new session; `lap_time_retracted` removes
a previously reported lap after a rewind. The consumer only reports a lap when
it observes consecutive lap numbers and a nonzero last-lap time. It cannot
recover laps missed before it starts or across skipped lap numbers, and car
indices do not by themselves establish driver identity.

The feature targets receive-to-processed p99 within 100 ms. Its full-corpus
acceptance runs are still required; the preliminary synthetic load model and
its Jeddah race results are recorded in `docs/lap-time-cost-experiment.md`.

### Record a session (Windows)

Point the game's UDP telemetry at the Windows PC, then:

```powershell
f1-rec record --out corpus\monza-race.f1r --forward 192.168.1.115:20777 --label "Monza race, full grid, dry"
```

`--forward` relays every datagram to the ingest as it arrives, so one session
produces both a corpus recording and a live archive. Ctrl+C stops recording;
`--duration-secs` stops it automatically.

### Stress-test the ingest from a recording (Windows → Ubuntu)

```powershell
f1-rec stress corpus\jeddah-race.f1r --target 192.168.1.115:20777 --speeds 1,2,4,8,16,max
```

Each speed is one run:

1. The sender asks the ingest to start a test run.
2. It replays the recording with the original timing, scaled by the speed.
3. It sends the count and digest of what it sent.
4. The ingest replies with a verdict:
   - whether the archived digest matches;
   - app drops, kernel drops and write errors;
   - whether consumers kept up;
   - written, durable and consumer latency percentiles.

A run passes if the archive is lossless, consumers kept up, and latency is
within `--max-written-p99-ms` (default 50) and `--max-durable-p99-ms`
(default 2000). The sweep stops at the first failure and reports the highest
passing speed. If the sender cannot reach a requested speed, the run is marked
inconclusive, never passed.

Other options:

- `--skip-secs` and `--limit-secs` replay a window of the recording.
- To model a planned feature's cost, start the ingest with
  `--synthetic-consumer-us <µs per packet>`.

Reports are written to `stress-results/<stamp>-<recording>.json`.

### Other commands

```text
f1-rec replay <recording> --target host:port [--speed 1|2|max]   plain replay, no verdict
f1-rec convert old.f1ecap new.f1r [--label ...]                   import a legacy capture
f1-rec info <file-or-dir>... [--json]                             sessions, packet counts, losses, digest
f1-rec verify <file-or-dir>...                                    check every block and the footer
```

## Validation so far

### Ubuntu host, sent from the Windows PC over the LAN (2026-10-26)

`f1-ingest` runs on Ubuntu as the user unit `f1-ingest` (started with
`systemd-run --user`; it is not yet a permanent service). It listens on
`0.0.0.0:20777`, archives to `/mnt/nvme/f1-engineer/archive`, and has
`--test-control` enabled. Linux reports socket drops, so kernel drops below
are measured, not unknown.

- **Acceptance baseline: the full Jeddah race (444,689 datagrams) at 1x and
  2x passes.**
  - Every datagram was archived, with zero app drops and zero kernel drops.
  - Socket queue peak was 8–15 KB; durable p99 was about 1 s (the fsync
    interval).
  - Sender lateness p99 was under 0.1 ms.
  - Report: `stress-results/20261026T064451Z-jeddah-race.json`.
- **Latency bug found by the baseline.** Written latency maxed at exactly 50 ms,
  because the writer was not woken when a new batch started.
  - After the fix, a 10-minute race window at 1x was lossless, with written
    p99 10.2 ms and max 13.2 ms (previously p99 30.9 ms and max 50 ms).
  - The full 1x/2x baseline has not been re-run since the fix.
- **Headroom.** The full race at 16x and 64x (about 20k datagrams/s) is
  lossless with zero kernel drops.
- **Unpaced speed (about 100k datagrams/s) loses datagrams on the network,
  not in the ingest.** Ubuntu's socket and UDP error counters did not move,
  and its NIC received only what was archived.
  - Ubuntu is connected by **Wi-Fi** (`wlp6s0`); the Windows PC is on wired
    gigabit.
  - Real sessions need about 300 datagrams/s (about 3 Mbit/s), far below that
    limit. Still, a wired connection would remove Wi-Fi loss as a variable for
    live sessions; the stress test cannot reproduce air-side loss.

### Windows development PC, loopback (development checks only)

- **Corpus conversion.** All four legacy captures convert and verify. Counts
  match the legacy tool exactly, e.g. 83,139 datagrams for Melbourne with
  identical per-packet-type totals.
- **Full race at 16x and 64x.** Lossless, run three times each.
- **Unpaced speed (about 100k datagrams/s).** Lossless, but written p99 was
  about 140 ms, so the run correctly fails the 50 ms budget.
- **Synthetic 20 µs/packet consumer.** It keeps up at 64x. At maximum speed it
  falls behind, and the verdict reports it while the archive stays complete.
- **Recorder with forwarding.** The recording and the ingest's live archive
  have identical digests.
- **Bug found by the stress test.** It caught 1–2 lost datagrams per full-race
  run, at random positions. The cause was the receive timeout on Windows. The
  receiver now blocks without one, and nine subsequent full-race runs were
  lossless.

## Contributing

See [AGENTS.md](AGENTS.md). Before committing, run `cargo fmt --check`,
`cargo clippy --all-targets` and `cargo test`.
