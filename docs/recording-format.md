# `.f1r` recording format (version 1)

The recorder, the ingest archive and the replay tools all use one format, so
any archived live session is also a replayable recording. All integers are
little-endian.

## File layout

```text
file header | block | block | ... | [footer | trailer]
```

### File header (32 bytes plus metadata)

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 8 | magic `F1RECv01` |
| 8 | 4 | metadata length `n` |
| 12 | 4 | flags (0) |
| 16 | 8 | time origin: Unix time in ns of record timestamp 0 |
| 24 | 8 | reserved (0) |
| 32 | n | metadata: UTF-8 JSON object (tool, version, label, role, ...) |

### Block (24-byte header plus body)

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | magic `BLK1` |
| 4 | 4 | record count |
| 8 | 4 | body length |
| 12 | 4 | CRC-32 (IEEE) of the body |
| 16 | 8 | timestamp of the first record |

The body is a sequence of records. Each block is written with one `write`. A
crash can therefore leave at most one partial block at the end of the file,
which readers detect and report as truncation.

### Record (12-byte header plus payload)

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 8 | receive time, ns since the time origin (monotonic clock) |
| 8 | 2 | payload length |
| 10 | 1 | kind |
| 11 | 1 | reserved (0) |
| 12 | len | payload |

| Kind | Payload |
| --- | --- |
| 0 | UDP datagram, byte-for-byte as received |
| 1 | stress-test control datagram: `u8` address length, source address text, datagram |
| 2 | loss marker: `u32` app-dropped, `u32` kernel-dropped datagrams just before this point |

Readers skip kinds they do not know.

### Footer and trailer (clean close only)

| Size | Field |
| --- | --- |
| 4 | magic `FTR1` |
| 4 | JSON length `m` |
| 4 | CRC-32 of the JSON |
| 4 | reserved (0) |
| m | JSON: `{summary, closed_unix_ns, extra}` |
| 8 | trailer: file offset of the footer |
| 8 | trailer magic `F1RECEND` |

The summary is the header index built while writing:

- Counts of records and datagrams, losses and rejected (non-F1 25) datagrams.
- The time span.
- A digest: the count plus the wrapping sum of the XXH3-64 hash of every
  datagram payload. It is order-independent, so a sender and a receiver can
  compare multisets cheaply.
- Per session UID: packet counts by type, frame and session-time range, game
  version and player car index.

`f1-rec info` reads the footer through the trailer without scanning.
`f1-rec verify` reads every block, checks CRCs and compares a rebuilt summary
with the footer. A file without a footer (killed writer) is still readable up
to its last complete block.

## Archive layout (`f1-ingest`)

```text
<archive>/live/<UTC start stamp>/seg-000001.f1r, seg-000002.f1r, ...
<archive>/tests/<run id>/seg-000001.f1r, ..., verdict.json
```

- Live segments rotate at `--segment-mib`. All segments of one ingest process
  share a time origin, so the files in a directory replay as one recording.
- Stress-test runs are kept apart from live data.
