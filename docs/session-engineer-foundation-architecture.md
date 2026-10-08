# Session engineer foundation: accepted architecture

Date: 2026-10-08

Status: implemented foundation, reviewed against the checkout in this thread.
Synthetic and localhost UDP validation is recorded separately in
[the validation/runbook](session-engineer-foundation-validation.md). Real-game
validation remains pending; this is not a claim that the milestone is complete.
No independent specialist review is claimed. The primary model was unchanged.

## Verified seams and authority

The approved product decisions and F1–F5 delivery order remain authoritative.
The implementation plan's contracts were proposals, refined as follows:

- TelemetryPipeline produces player samples, all-car observations, attempts,
  participant tenures, context and lifecycle outputs.
- Managed recording's acquisition-observer path is not a durable analysis sink.
  The automatic runtime now owns UDP and invokes the full pipeline directly.
- The finalized-capture importer owns archive trace writing. Its sample/sealing
  ordering is extracted into processing.output_consumption and reused by the
  coordinator. The archive writer and the live ledger have different storage
  representations, not different measurement or telemetry processors.
- Legacy archive readers require complete processing runs. That remains intact.
- Legacy player-to-car-slot authority is deliberately restricted. It is not
  expanded to authorize the new session measurement policy.

## ADR 1 — Additive session ledger, not active archive imports

Use a separate SQLite sidecar, normally <archive-stem>-evidence.sqlite3. The
legacy database, capture files, import hashes, checksums, complete-run predicates
and trace ownership checks are unchanged. Schema version 2 adds logical session
occurrences independent of capture closure and processing-run identity.

Logical sessions are scoped by processing generation, observed game UID and
occurrence. An authoritative SSTA for the current ended UID creates another
occurrence. Ended/retired inputs cannot reactivate previous sessions. UID zero
cannot establish a session. Do not infer career/save identity or merge archive
reprocessings into live sessions speculatively.

Rationale: a completed lap can be available while its session and acquisition
remain active. Neither final capture provenance nor a completed archive run is
an appropriate authority for active evidence.

## ADR 2 — One ordered coordinator, durable admission before publication

LiveSessionRuntime and finalized capture replay both feed SessionCoordinator.
The coordinator invokes the existing deterministic pipeline and shared output
consumer. One OS-locked persistence owner per sidecar/source performs serial
admission and publication. The UDP callback only enqueues; a single persistence
executor keeps disk work off the receiving event loop.

Raw datagrams and explicit gap/end entries are journaled with source metadata,
ordered ledger sequences and SHA256 payload checksums before processing.
Publication then commits chunks, attempt manifests, readiness dispositions,
metadata and the processed checkpoint in one transaction. A failure closes
admission until recovery; a committed journal tail is replayed on restart.

Recovery streams 100 journal entries at a time. It reconstructs pipeline state
from the generation's admitted prefix without rewriting already committed
outputs, then publishes the uncommitted tail. This is bounded-memory but O(total
admitted journal) startup, not an O(1) serialized pipeline snapshot. Processor
versions are pinned; incompatible generations are not silently resumed.

Rationale: the pipeline's ordering/ownership state must be rebuilt exactly;
resuming at a file offset without that state would misattribute laps. Raw
journaling provides reproducibility without concurrently importing open captures.

## ADR 3 — Immutable committed SQLite chunks instead of open trace files

Refine the proposed separate-file publication protocol: sealed JSON chunks of
at most 256 rows live in the SQLite ledger, addressed by their SHA256 content.
The immutable manifest pins chunk hashes, row/byte counts, source ranges,
packet format, lifecycle epoch, driver binding and qualifications. SQLite WAL
commit is the publication boundary, eliminating rename/manifest coordination
across files. Readers use a committed snapshot and verify hashes, byte/row
counts and sample ownership. Open staging rows are never served as lap evidence.

UPDATE and DELETE triggers protect journal entries, chunks, manifests,
dispositions and saved comparisons. Operational staging/session projections are
mutable. Flashbacks and scope problems append superseded/quarantined readiness;
they do not rewrite manifests or old reports. A saved report pins both revisions,
manifest hashes, readiness sequences and measurement policy/configuration.
Consumers must inspect current attempt readiness before reusing old references;
the saved report remains the exact historical result, not a silently refreshed one.

Rationale: transactional chunks retain the same sealed-evidence guarantees as
files with fewer partial-publication failure states. Historical evidence belongs
on disk, not in a growing Python lap history.

## ADR 4 — Session-scoped association and separate states

A driver association hashes logical session, lifecycle epoch, slot, tenure
ordinal and observed participant identity fingerprint. Names and slots are
attributes, not permanent identity. Full-lap ownership requires one binding
covering the complete admitted attempt range in the same epoch and packet
format. Active binding extents are monotonic, including telemetry-only outputs.
Roster ambiguity, reconnects and incomplete scope quarantine evidence rather
than borrowing a previous driver's identity. This conservative foundation does
not yet assert identity continuity across ambiguous slot migrations.

Keep acquisition (listening/receiving/stale/stopped/failed), logical session
lifecycle (active/interrupted/ended), processing generation and per-attempt
readiness distinct. Silence, socket errors and known queue loss insert explicit
gaps and interrupt association; silence is never proof of session end. Listener
shutdown interrupts rather than fabricating a game end. Fresh participant
association is required after a gap. UDP losses invisible to the socket/sequence
counter cannot be claimed to have been individually observed.

Session-context history and accepted raw progress fields are retained. Unknown
or truncated context stays explicit. The progress fields do not establish
classification neighbours, qualifying stages, save identity or Time Trial rival
telemetry capability by themselves.

## ADR 5 — Explicit bounded observed measurement policy

Introduce observed-session-distance-v1 through an EvidenceProvider seam. Live
and review invoke the same deterministic measurement function. Keep the legacy
practice/qualifying diagnostic policy unchanged. Comparisons require published,
owned completed attempts from the requested session and compatible known track
geometry/packet format; unknown context is qualified, not invented.

Reuse resampling, delta-time, channel-difference and sustained-threshold event
primitives. The distance grid is 5 m, capped at 5,001 points. Interpolation uses
the existing 0.1 s/25 m bracket limits; missing channels/coverage remain missing.
Braking onset is the first brake application >=10% sustained >=0.1 s. Symmetric,
overlapping numeric distance regions match zones without track authoring or a
model. Both drivers use the same rule. Missing, left-censored or ambiguous
onsets are not asserted; source brackets produce an uncertainty interval.
Later braking is not automatically better. Fuel/tyre uncertainty, pit state,
validity, different epochs and unavailable geometry qualify measurements rather
than manufacturing causes or globally blocking supported channels.

Reports are saved only after rechecking readiness inside the commit transaction.
No model, named-corner inference, reference ranking or radio/task behaviour is
introduced in this milestone.

## ADR 6 — Bounded resources, explicit operational limits

- Default automatic UDP queue: at most 1,024 datagrams; direct runtime maximum
  4,096. Raw admission: at most 65,535 bytes and 255 host characters/datagram.
- Coordinator frame assembly: 64 open frames, 512 pending packets, reorder
  window 3. Legacy pipeline defaults remain unchanged.
- History: 512 session context entries, 64 active-attempt context segments,
  bounded diagnostic histories and 128 cached session/UID mappings. Finalized
  attempts are drained after durable consumption.
- On-disk staging: 40,000 rows per car, retaining explicit missing-row
  qualifications when a long attempt exceeds that horizon.
- SQLite cache: 4 MiB/connection. Recovery pages: 100 journal entries.
- Evidence read: 20,000 rows and 16 MiB/attempt; pages: 100; API admits at most
  two concurrent analyses/trace reads. These are upper bounds, not peak RSS
  claims; decoded objects and native allocations add overhead.

Disk journal/history intentionally grow with recorded evidence. No destructive
retention/compaction policy is introduced. Operators must provision disk and
retain the sidecar including its WAL consistently. Startup replay latency and
full-rate real-game throughput are outstanding operational validation gates.
The measured synthetic budgets do not certify the worst-case memory bounds or
full packet mix. Failure marks acquisition failed rather than serving a partial
manifest. Tightening or batching durability must preserve replay/gap semantics.

## Migration and future extension boundaries

Existing archives need no migration. A read-only paged adapter lists only
complete runs with complete captures, uses archive:<session_key> IDs and directs
analysis to the existing integrity-checked archive APIs. Prototype ledger schema
1 migrates transactionally to schema 2, preserving session IDs as occurrence 1.
Back up a sidecar before migration; unsupported versions fail explicitly.

The new APIs are under /api/v2/session-evidence. Automatic UDP starts with the
API lifespan; --manual-acquisition preserves the legacy recorder workflow for
diagnostics. Manual UDP recording is refused while automatic acquisition owns
the socket. UI navigation and full radio/task/conversation work are deferred.

Future targets/tasks can pin logical-session driver associations and evidence
revisions, with separate policies for race/qualifying/practice/Time Trial. Driver
tracking across ambiguous association changes, classification neighbour rules,
qualifying stages and detailed in-game rival telemetry remain unresolved
capabilities, not facts implied by this foundation. The assigned audit files
were neither edited nor depended upon.
