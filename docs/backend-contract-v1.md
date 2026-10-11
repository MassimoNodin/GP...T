# Backend evidence services contract v1

Implementation authorized October 11, 2026, excluding UI and deployment.

## Identity and eligibility

Providers expose attempt(id, session=...), attempts(session, limit=100, after=''), and evidence(id, session=...). Published source revisions are immutable. Derived evidence is ephemeral in separate temporary storage and projected to the requested original session/revision with explicit materialization provenance. Never modify source dispositions, chunks, reports or generation settings.

Reference selection is diagnostic-only, same verified driver/car/logical session/epoch/format and stable compatible context. Require completed, positive timed, game-valid, observed-start, pit-free published laps with no acquisition gap, missing rows or unknown context. Explicit references are validated without replacement. No eligible reference is normal unavailability. Automatic selection scans at most 512 candidates; overflow fails closed. No metadata-only trace or deferred candidate is ranked as usable evidence. Coaching and competitive ranking remain ineligible until their independent admission requirements are supported.

## Frozen budgets and persistence

- One historical materialization at a time per API instance, no queue; busy requests fail immediately.
- Replay begins at generation sequence 1, never a guessed packet boundary. Maximum 100,000 journal entries, 128 MiB journal payload plus metadata, 30 seconds wall clock, 256 MiB temporary SQLite files. Exceeding any limit fails closed. This intentionally rejects oversized historical generations rather than offering unsafe partial replay.
- Replay all recorded input routes, source changes, acquisition gaps and lifecycle markers; skip only Gate A detail-demand commands because full replay has no selective leases. Preserve original datagram metadata. Use separate full-profile temporary evidence storage and existing processor version; unknown journal kinds/versions fail closed.
- Materializations are request-scoped and ephemeral. Completed source attempts may be reconstructed from a committed prefix of an active generation; never include uncommitted journal input. No background workers, durable jobs, additional threads or changed receiver settings. Cancellation and timeout are checked between entries; temporary resources close on every exit.
- Source evidence reads retain the existing 20,000-row / 32 MiB caps. Derived evidence uses the same read limits. No unbounded source read, historical candidate scan or in-memory result store.
- Measurement cache: 32 entries / 16 MiB canonical JSON bytes, at most two in-flight keys, 30-second duplicate wait maximum, copied values and LRU eviction. Oversized results fail explicitly. Only immutable measurement results are cached; reference selection is revalidated per request.
- Engineer requests: explicit only, maximum question 2,000 characters. Ordinary revision requests have a 30-second lifetime. Explicit future-binding requests have a 270-second lifetime with at most 240 seconds waiting for a complete lap, polling at 0.1 seconds. This reviewed exception is necessary because an ordinary request deadline is shorter than a flying lap. Preserve Gate A's existing 300-second lease TTL, with no renewal/retry loop. Enqueue release on exit and report cleanup rejection; enqueueing is not proof of owner-applied cleanup. Acquire process AI admission before any model invocation. Historical reconstruction requires explicit request opt-in. No model invocation for unavailable evidence.

## Service results and failures

Synchronous materialization returns an available derived trace or raises EvidenceUnavailable with stable materialization_* reasons. No new queued/running job protocol is exposed. Existing live detail task states remain unchanged. Reference selection returns available/unavailable with a reason, chosen revision, exclusions, candidate-set hash and policy version. Measurement and engineer results explicitly set diagnostic_only=true, coaching_eligible=false and ranking_eligible=false.

Cancellation is cooperative between journal entries; a single decode/SQLite operation is not preempted. Busy limits are process-local, not cross-process scheduling. Every source lookup validates the caller's session and identity; repeated numeric UIDs never identify logical occurrences.

## Integration ownership and endpoints

Primary owns EvidenceStore metadata lookup, historical replay, API wiring and integration tests. Delegated agents own only their approved new service/test files. New operations are under /api/v2/session-evidence/sessions/{session_id}; mutations/expensive requests require the existing control token and comparison admission. Existing routes and default acquisition/profile behavior do not change. No production listener or database is used for tests.

Linux recovery/load and real-game coexistence remain operator release gates. Unit/synthetic checks do not imply zero drops or deployment acceptance.
