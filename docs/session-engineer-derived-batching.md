# Derived persistence batching: option 2

Date: 2026-10-09

Status: correctness and sustained baseline-load validation passed; the complete
throughput acceptance gate has **not** passed. This is not a real-game acceptance
claim or completion of the foundation milestone.

## Accepted architecture

Keep one serialized coordinator and SQLite writer. Raw UDP admissions still
commit individually with WAL/FULL durability, before processing. Never hold a
derived transaction open across a journal admission and never switch raw
journaling to NORMAL/OFF durability.

Add separate admitted and processing sequence cursors. `journal(raw)` appends
without projecting; `publish_pending()` reads at most 32 admitted journal rows
and applies the existing pipeline/output consumer inside one derived
transaction. Existing `ingest`, `gap` and `finish` remain synchronous adapters;
gaps and finish first publish any preceding admitted tail.

The live owner journals each dequeued datagram, then schedules publication when
32 rows accumulate or the oldest pending admission reaches 20 ms. Idle input
also flushes on that cadence. These are **row and scheduling bounds**, not a
guarantee that a lap-sealing transaction takes less than 20 ms. A publication
still blocks this one writer; no independent journal-only throughput or
bounded-latency guarantee follows from the split. Queued pre-journal input
remains bounded and can still overflow under excessive offered load.

Rationale: reduce derived fsyncs without changing raw durability or creating a
second analysis implementation. Each batch advances the committed checkpoint
atomically with chunks, manifests, dispositions and staging. Readers see only
the previous or fully committed batch. Historical processing state is rebuilt
from disk, not retained as output queues. At most 32 maximum-sized datagrams
are loaded for projection, about 2 MiB of raw payload before object/copy costs.
The existing pipeline, staging, SQLite and analysis limits remain in effect.

## Lifecycle routing and compatibility

Admission no longer consults lagging pipeline/lifecycle state. Processing
classifies UID zero, ended/retired inputs and authoritative same-UID SSTA in
journal order, using the session state updated by earlier rows in the same
transaction. Non-default routing decisions are appended as `input_route`
metadata with the projection checkpoint. Committed-prefix reconstruction
uses those decisions; unpublished tails derive them from checkpointed state.
Legacy routing flags already stored in journals remain authoritative on replay.
This avoids consulting final session state when reconstructing earlier input.

No schema migration or measurement-policy expansion is required. Processor
version remains unchanged because normalized evidence/eligibility must match
the synchronous adapter. Raw checksums, immutable manifests, tenure ownership,
flashback and missing-coverage safeguards stay intact. No sealed-fragment
inventory, raw commit batching, archive changes, radio or UI work is included.

## Failure, stop and metrics

Journaled but unpublished data is recoverable; it is not source evidence lost
on a process crash. Projection failure rolls back the complete batch, clears
the staging-count cache and requires reconstruction before further admission.
Recovery rebuilds committed history and publishes the journal tail in batches.
Closing a coordinator without publication leaves that tail for recovery.

Normal runtime stop closes UDP, publishes admitted pending input and records
an explicit gap if unadmitted queued input is discarded. It then records
listener stop without declaring the game session finished. Silence/reconnect
and actual packet loss continue to fence ownership; publication delay alone
does not invent a gap.

Runtime status separately reports journaled/processed datagrams, pending
publication, current/max receive queue depth, last journal commit, derived
publication and gap duration, batch sizes and oldest-datagram publication
delay. Recovered historical rows are not counted as newly received traffic.

## Reproduction

```powershell
uv run --frozen pytest tests/test_session_evidence_batching.py tests/test_session_evidence.py -q
uv run --frozen python -m scripts.benchmark_session_publication --duration-s 90 --batch-size 32
uv run --frozen python -m scripts.benchmark_session_publication --duration-s 90 --batch-size 1
uv run --frozen python -m scripts.benchmark_session_publication --journal data/session-engineer-live-pruning-20261009-snapshot.sqlite3
uv run --frozen pytest -q
```

The load harness uses advancing lap distances/times and seven valid packet
kinds for 22 cars, not repeatedly discarded unchanged headers. At 355 packets/s
it adds a 530 packets/s burst between seconds 30 and 40. It binds only a fresh
localhost ephemeral port, never the game's port. Short synthetic laps exercise
publication frequently; this is not the exact real-game packet mix. Timing
percentiles are sampled runtime metrics, not an exhaustive packet trace. JSON
results, sampled status and ledgers stay in a new ignored data directory.

The replay harness streams every admitted real datagram **and every recorded
gap** into both synchronous and batched consumers. Its normalized fingerprint
includes session state, attempts, manifests, ownership, disposition history,
chunk checksum validation and remaining staging, excluding operational IDs.
It must match; the recording cannot recover packets lost before journaling.
Synthetic comparisons separately verify deterministic measurements, since the
failed real recording has no eligible completed-lap comparison.

## Measured results (2026-10-09)

Focused publication, recovery and staging regression tests: **45 passed** in
41.74 s. This includes all five injected failpoints, recovery of an unpublished
multi-row tail, committed-reader visibility, serial/batched measurement parity,
missing brake channels, actual gaps and delayed SEND/SSTA/retired-UID routing.

Full Python regression suite: **968 passed, 10 skipped** in 190.98 s using
`uv run --frozen pytest -q -rs`. Skips are existing Windows symlink privilege
and platform-specific POSIX/handle-pinning cases. One existing Starlette/httpx
deprecation warning remains. This covers legacy archive safeguards and the
existing ordering, flashback, wraparound, tenure and lap-reset regressions.
The memory-retention guard now exercises both synchronous and batched paths.
An isolated batched 24-driver/600-frame tracemalloc run passes in 30.56 s:
Python peak is 1,426,071 bytes; retained traced state is 94,747 bytes at 200
frames and 127,855 at 600 (33,108 bytes growth). This is not native/RSS or a
race-length measurement, and it does not measure worst-case full queue/read
allocations. Loaded publication rows remain capped at 32 and histories are
released after each processed row, not just at the end of a batch.

Faithful replay of the October 9 failed practice recording preserves all
18,200 admitted datagrams and 5,437 gap records. Serial publication takes
53.39 s (340.89 datagrams/s); batching takes 45.09 s (403.61 datagrams/s),
**15.5% less elapsed time**. Normalized evidence/eligibility fingerprints are
identical. This is an isolated replay, not acquisition while the game runs;
it cannot restore the missing UDP inputs or produce an eligible comparison
from this gapped recording. Results are in
`data/publication-benchmark-real-replay-20261009/summary.json`.

| Paced synthetic run | Received / processed | Drops | Maximum queue | Sampled oldest-delay p95 / max | Sampled publication p95 |
| --- | --- | --- | --- | --- | --- |
| Batch 1 control, 90 s at 355/s, 150-frame laps | 31,950 / 31,832 | 118 | 1,024 | 2,923.73 / 3,150.21 ms | 5.02 ms |
| Batch 32, 90 s at 355/s, 150-frame laps | 31,950 / 31,950 | 0 | 176 | 290.38 / 542.09 ms | 43.84 ms |
| Batch 32, 90 s with 10 s at 530/s, 150-frame laps | 33,700 / 33,307 | 393 | 1,024 | 1,864 / 2,630 ms | 46.52 ms |
| Batch 32, earlier 530/s burst, 20-frame laps | 33,700 / 33,308 | 392 | 1,024 | 1,744 / 2,676 ms | 54.09 ms |

The steady run retains 630 opponent and 30 player completed laps. Its active
comparison uses 150 rows per driver, coverage 1.0 and supported brake-onset
zones; ingestion advances from 2,447 to 2,513 processed packets during the
comparison. The exact saved report survives runtime stop. Drain is 40.45 ms;
the final ledger is 206,856,192 bytes. The 21 opponent and one player open
tails are quarantined at stop rather than invented as completed laps.

Artifacts are respectively under
`data/publication-benchmark-batch-steady-90s-20261009`,
`data/publication-benchmark-batch-150-90s-20261009` and
`data/publication-benchmark-batch-90s-20261009`. They are ignored local evidence,
not committed captures. The burst failures are not dismissed as tiny-lap
artifacts: the 150-frame fixture also overflows.

The final isolated 90 s batch-1 control drops 118 packets at the same 355/s
steady offered load, versus zero with batching. Its saved comparison survives,
but later queue gaps interrupt evidence and the lossless-load check fails.
Its empty queue at the end does not undo those gaps. Results are in
`data/publication-benchmark-serial-steady-90s-final-20261009/summary.json`.

Earlier 8 s serial/batched controls both process 2,840 packets without loss,
but batching reduces maximum queue from 152 to 19 and drain from 468.8 to
81.6 ms. These short controls are supporting observations, not the sustained
acceptance test. A subsequent serial 90 s run cannot find completed lap 2;
the harness now records unavailable comparisons as a failed result instead
of throwing away the status summary.

The harness's `passed` flag covers lossless ingestion and retained comparison,
**not all investigation-report performance thresholds**. Even the steady
run misses the proposed 250 ms p95 backlog goal (290.38 ms), and maximum queue
176 does not establish the under-128 steady-depth target. The 530/s burst
fails losslessness and the one-second maximum backlog goal. Publication p95
passes 50 ms for the 150-frame runs. Working-set/RSS, hard process kill,
filesystem-full, power-loss and race-length disk/restart budgets are not
certified by these results. Per-datagram FULL durability is unchanged.

## Remaining gates

Treat batching as an incremental improvement, not a complete throughput fix.
Do not claim the investigation's full idle-host gate or foundation milestone
complete. Another real-game test must still verify zero drops through two
uninterrupted flying laps, eligible opponent evidence and an active comparison;
CPU/disk contention from F1 25 may erase the synthetic headroom. Raw commit
batching or reduced durability requires a separately reviewed loss contract;
this change does not silently adopt either option.
