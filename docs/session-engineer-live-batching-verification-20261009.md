# Derived batching: real-game verification

Date: 2026-10-09

Result: **failed live throughput and completed-lap comparison acceptance**.
This is a verification record, not a new processing change or milestone
completion claim.

## Experiment

Tested feature-branch commit `db094fdfc6d0715f40a54a434ef47678bd5900a7` while
the user ran F1 25. The observed context identifies packet format 2025,
Austria (track 17, 4,323 m), short practice, Grand Prix game mode and the
practice/qualifying rule set. Game telemetry-rate and privacy menu settings
were not independently inspected.

Port 20777 was free before starting. A fresh production `LiveSessionRuntime`
listened on `0.0.0.0:20777`, using the default 1,024-entry receive queue,
per-datagram WAL/FULL journaling and bounded derived publication. The observer
sampled status every approximately 250 ms and searched committed evidence
every five seconds for a published player/opponent pair with over 100 rows
per driver in an active session. No competing API listener or synthetic
sender was started.

The observer stopped at approximately 90 s because sustained overload had
already established failure; total time including shutdown/snapshot was
92.53 s. It did not wait indefinitely for complete laps across known gaps.
Input slowed temporarily near seconds 80-86, so this is not a claim that two
uninterrupted flying laps were observed.

## Measurements

| Measurement | Observed result |
| --- | --- |
| Received datagrams | 29,302 |
| Journaled/projected datagrams after stop | 20,968 / 20,968 |
| Receive-queue overflow drops | 7,822 (26.69% of received datagrams) |
| Additional unadmitted entries discarded at stop | 512, with an explicit shutdown gap |
| Maximum receive queue | 1,024; saturated by the first 15 s status sample |
| Sampled receive-rate median while receiving over 300/s | 356 packets/s |
| Sampled processing-rate median in those same intervals | 242.75 packets/s |
| Sampled oldest-datagram publication delay p95 / max | 5.373 / 5.797 s |
| Sampled publication-duration p95 | 53.71 ms |
| Largest observed publication batch | 26 rows (configured cap 32) |
| Socket / persisted decode errors | 0 / 0 |
| Associated car slots | 20 (slots 0-19); not cross-session permanent identities |
| Snapshot size | 240,209,920 bytes |

An early process sample showed working set 51,240,960 bytes and process peak
working set 51,572,736 bytes **at that observation**. This is not the peak
over the complete run or a race-length memory certification. Timing metrics
are sampled last-operation values, not exhaustive per-packet distributions.
Receive statistics cannot establish that the game/network delivered every
packet it generated. The admitted packet mix is saved in the integrity audit;
it cannot reconstruct the mix of dropped packets.

## Evidence, integrity and cleanup

No measurement-ready laps or comparison were produced. The ledger retains
1,480 quarantined player attempts (maximum 656 rows) and 22 quarantined
opponent attempts (maximum 561 rows). Do not bypass readiness or association
guards to turn these fragments into complete comparisons.

The journal has 1,645 gap entries: 1,642 queue-pressure/socket-sequence gaps,
one listener start, one explicit shutdown discard and one listener stop.
All admitted pending publication was flushed before shutdown; the final
committed checkpoint and maximum journal sequence both equal 22,613.
Generation state remains `running` and session state is `interrupted/stale`:
listener shutdown is not evidence of a game session ending.

SQLite `quick_check` returns `ok`. All 20,968 raw payload checksums and 1,508
sealed chunk checksums/byte counts/row counts verify. Runtime shutdown reports
`stopped`, no error, zero pending publication and an empty queue. A subsequent
Windows endpoint check finds zero listeners on UDP port 20777.

Artifacts remain in the ignored local directory:

`data/session-engineer-live-batching-20261009-173036/`

- `summary.json`: final counters, context, readiness and sampled latency.
- `observations.jsonl`: bounded-duration status samples.
- `evidence.sqlite3`: original journal and evidence ledger.
- `snapshot.sqlite3`: consistent SQLite backup taken after stopping.
- `integrity-audit.json`: checksum checks, admitted packet mix and rate summary.

No comparison file exists because no eligible pair was found. The snapshots
and previous October 8/9 recordings were not overwritten. No raw evidence is
committed to Git. No replay-parity result for this new capture is claimed.

## Interpretation

The previous option-1 live run on `c5bc20b` had 21,771 drops out of 39,191
received packets (about 55.6%) at its last recorded sample, with sampled
processing rates 87-142/s. This run has a lower observed overflow fraction
and higher typical processing rate. That is encouraging, but these are
different-duration runs under uncontrolled game/host load, not a controlled
A/B estimate or proof of a particular speedup.

Derived batching still leaves publication on the sole persistence writer's
critical path. The idle synthetic success did not transfer to lossless live
acquisition. The live losslessness, latency and active-comparison gates remain
failed. Any further fix must preserve raw durability, explicit gaps, committed
reader visibility, ownership and legacy archive safeguards. Raw-commit batching
or reduced durability must not be silently enabled by this failure report.
