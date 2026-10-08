# Staging pruning: first throughput fix

Date: 2026-10-09

## Accepted decision

Keep the existing retention contract: at most 40,000 staged rows per
processing generation, game session UID and car, keeping the greatest frame
ordinals. Do not replace it with a 40,000-frame horizon; sparse observations
make those policies different.

The serialized coordinator maintains an LRU cache of exact staging row counts,
bounded to 128 keys. On a cache miss it counts committed/current-transaction
rows once. Inserts distinguish new rows from replacements, and publication
deletions subtract the actual deleted row count. Under the cap, pruning issues
no SQL. Over the cap, an ascending indexed lookup skips only the excess rows
and deletes that oldest prefix, rather than scanning the retained 40,000 rows.

Transaction failure clears the cache after rollback. Restart and eviction
rebuild counts from SQLite. The cache is an optimization, not durable evidence
or authority: the existing exclusive coordinator ownership remains required.
All staging writes remain owned by that coordinator.

Rationale: eliminate repeated below-cap scans without changing ownership,
coverage qualifications, publication ordering, source journaling, FULL
durability, checkpoints, reader integrity or legacy archive behavior. Existing
schemas and processor versions are unchanged; no migration is required.

## Reproducible synthetic validation

Run:

```powershell
uv run --frozen pytest tests/test_session_staging_retention.py -q -s
uv run --frozen pytest tests/test_session_evidence.py tests/test_session_evidence_load.py -q -s
uv run --frozen pytest -q
```

The new suite exercises sparse, duplicate and out-of-order frames, exact caps,
zero repeated below-cap pruning/count queries, rollback, LRU eviction,
cross-session/car isolation, publication deletion accounting and restart.

Its A/B benchmark seeds 6,232 synthetic rows for each of 22 cars, then writes
50 more frames. Both strategies use identical insert bookkeeping; only the
pruning strategy differs. Resulting frame/payload rows must match exactly.
SQLite progress callbacks every 1,000 VM operations measure SQL work; the
acceptance assertion requires at least a 20-fold reduction, not a flaky wall
clock speed threshold. Setup and initial count loading are excluded; the
measured transaction includes its commit. Payloads are minimal dummy JSON.

Initial local run: legacy 753.87 ms and 20,733 progress callbacks versus cached
9.99 ms and 39 callbacks for all 50 frames. This is about 75-fold faster for
this isolated staging workload and about 532-fold fewer progress callbacks.
These are local observations, not production budgets.

A repeat measured legacy 762.80 ms versus cached 14.22 ms (about 54-fold
faster), with the same 20,733 versus 39 progress callbacks. Wall time varies;
the deterministic work and retained-row assertions are the acceptance gates.

Final code run: legacy 812.20 ms versus cached 11.34 ms (about 72-fold faster),
again with identical retained rows and 20,733 versus 39 progress callbacks.
All seven focused staging tests pass. The publication/replay/recovery suite
also passes, and the final full suite reports **953 passed, 10 skipped** in
127.83 seconds. The only warning is the existing Starlette/httpx deprecation.
The three synthetic load/memory tests pass separately in 40.43 seconds.

The 24-driver synthetic end-to-end load test published 1,176 attempts over
1,000 frames / 2,001 datagrams with 19 concurrent comparisons and 408 staged
rows left. It measured 164.59 datagrams/s, p95 ingest 13.48 ms and maximum
53.14 ms. The separate 600-frame memory test measured a 1,364,000-byte Python
allocation peak and retained-state growth of 45,876 bytes between checkpoints.
That memory measurement is tracemalloc, not whole-process RSS. This packet mix
is not the full real-game mix and this throughput does not establish the
approximately 355-datagram/s real-game target.

## Limits

This addresses only repeated staging scans. It does not batch derived commits,
change writer scheduling or introduce indexed sealed fragments. Open staging
can still reach its existing row cap and historical truncation still receives
existing missing-row qualifications. Journal commit latency and large payload
serialization remain potential bottlenecks.

Synthetic tests do not establish that the full mixed-packet UDP workload now
keeps up with the game. Fresh real-game completed-lap publication/comparison
and full-rate acquisition acceptance remain outstanding. No game is needed to
run these regression tests, and no live-game receiver is started for this
experiment. The standard suite retains its localhost UDP fixture coverage.
