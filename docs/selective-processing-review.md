# Selective processing: independent primary review

Reviewed and corrected on October 11, 2026. This supplements, rather than
rewrites, Luna's original implementation handoff and the locked plan.

## Findings and corrections

1. **Runtime crash:** status collection touched the persistence owner's SQLite
   connection from the asyncio thread. Status is now collected on the owner
   executor; callers receive copied snapshots, including nested selections.
2. **Batching regression:** the receive loop unconditionally published pending
   data every iteration. Normal count/time batching is restored; only actual
   demand mutations or due expiry trigger a mutation barrier. The full profile
   does not service the demand queue on each tick.
3. **Nondeterministic recovery:** replay resolved targets against the final DB
   binding extent and latest logical-session occurrence. Targets now resolve
   against historical pipeline tenures and journaled logical-session identity.
   Lease deadlines are recorded with commands. Committed demands are replayed
   for the journal tail, then cleared before new ingestion.
4. **Incomplete traces could publish:** coverage counted frame ordinals,
   including frames without Lap Data, and scanned the entire selection history
   for each opponent lap. Bounded per-active-lap omission counters now count
   actual admitted lap samples. Deliberate partial selection is deferred;
   unexplained missing rows and acquisition gaps take quarantine precedence.
5. **Lost selection events:** multiple changes in one flush reused metadata
   ordinal zero. All changes now persist, with their frame-specific tenures,
   rather than binding earlier events to the final frame's occupants.
6. **Lease queue edge cases:** queued API state could change owner decisions;
   owner application did not revalidate task identity; release could crash after
   snapshot eviction; coalescing could lose a post after a queued release.
   Applied state is now separate, identity is rechecked, release is safe without
   a retained snapshot, and coalescing preserves per-task command order.
7. **Premature/stale status:** task/target/coverage snapshots are held behind the
   publication transaction boundary. Failure and shutdown resolve queued work
   and terminate active leases. Stopped runtimes reject new commands. Target
   discovery follows only the current logical session, retains the correct
   player identity on non-lap frames, and invalidates on idle gaps. Selection
   clears on acquisition interruption; counters remain monotonic on restarts.
8. **Policy/test correction:** automatic neighbors require a verified player
   tenure. The single-other-car traffic test now correctly expects both ahead
   and behind reasons, deduplicated to one detailed car.

## Independent validation

- Added 19 regression cases in tests/test_detail_review.py: owner-thread SQLite
  use, full/demand batching, snapshot isolation, queue ordering and identity,
  eviction-safe release, shutdown, publication barriers and commit visibility,
  four crash boundaries with exact chunk/readiness equivalence under a different
  replay clock, sparse non-lap frames, real-loss quarantine precedence,
  multi-event flushes, repeated UID occurrences, idle gaps, and 24-slot inventory.
- Full final Python suite: **1,184 passed, 16 skipped**, two dependency
  deprecation warnings, 196.10 seconds.
- Task-related git diff whitespace validation passed.

## Scope and remaining checks

- Full remains the default; demand_v1 remains explicitly opt-in. Raw ingestion,
  whole-field lap metadata, receiver isolation, queue/buffer limits, and the
  protected evidence-read budget are preserved. No dependencies, schema
  migration, web code, production settings, commits, or branches were changed.
- Linux execution and real-game/Wi-Fi kernel-drop measurements were not run in
  this Windows review. Passing synthetic tests does not prove zero live drops.
- Existing immutable evidence created by the flawed implementation is not
  rewritten. Ambiguous legacy demand-session replay fails closed; use a fresh
  operator-selected demand generation/database rather than trusting or silently
  repairing previously published incomplete traces.
- Historical backfill, AI/LLM demand integration, and UI task controls remain
  outside Gate A and outside this review.
