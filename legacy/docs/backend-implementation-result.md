# Backend-only slices: implementation result

Completed development implementation on October 11, 2026, with four delegated
service/test workers and primary-owned contracts, historical replay and API wiring.
This is not Linux/live-load acceptance or production deployment approval.

## Delivered slices

| Slice | Result |
| --- | --- |
| 0 | Frozen identity, eligibility, persistence, cancellation and budget contracts in backend-contract-v1.md |
| 1 | Bounded committed-prefix historical replay into separate temporary evidence; exact occurrence/tenure/attempt matching; stable content-addressed provenance; source evidence unchanged |
| 2 | Metadata-only deterministic player reference selection with strict context/quality admission, explicit rejection, exclusions and candidate-set hashes |
| 3 | Bounded copied-value LRU, byte/entry ceilings, two-key single flight, version/payload/provenance keys and failure cleanup |
| 4 | Existing numeric-distance comparison and braking-region measurements adapted to the new provider, with fresh eligibility on cache hits |
| 5 | Explicit diagnostic engineer requests, opt-in historical detail, evidence-gated model intent routing, and activation-qualified future trace summaries |
| 6 | Authenticated backend routes, shared comparison admission, copied diagnostics, disconnect cancellation and integration regressions |

No frontend, receiver, deployment, dependency/lockfile, default-profile, branch or
commit changes were made. Pre-existing README, CLI, migration, UDP-replay and web
work was preserved. No production database or listener was used for tests.

## Changed paths

Existing source changes are limited to f1_engineer/api/app.py (additive service
wiring/routes) and f1_engineer/processing/evidence.py (metadata lookup and tenure
verification).

New services:
- f1_engineer/processing/materialization.py
- f1_engineer/analysis/session_reference.py
- f1_engineer/analysis/evidence_cache.py
- f1_engineer/analysis/session_measurement.py
- f1_engineer/analysis/session_engineer.py

New focused/integration tests:
- tests/test_materialization.py
- tests/test_session_reference.py
- tests/test_evidence_cache.py
- tests/test_session_measurement.py
- tests/test_session_engineer.py
- tests/test_backend_services_api.py

Documentation: backend-agent-slices.md was updated to record authorization;
backend-contract-v1.md and backend-services.md define the actual contract and
operator-facing behavior. This file records the development handoff.

## Validation history

- First complete Python run: 1,555 passed, 16 skipped, two existing deprecation
  warnings, 213.80 seconds.
- Complete run after the reviewed future-request timing change: 1,556 passed,
  16 skipped, two existing deprecation warnings, 231.17 seconds. Command:
  uv run --extra dev --extra app pytest -q --tb=short
- Final focused regression run, including subsequent stable-provenance and
  malformed-journal hardening: 337 passed, one existing deprecation warning,
  44.47 seconds. Command:
  uv run --extra dev --extra app pytest tests/test_materialization.py tests/test_backend_services_api.py tests/test_evidence_cache.py tests/test_session_reference.py tests/test_session_measurement.py tests/test_session_engineer.py -q --tb=short
- Task-scoped tracked and new-file whitespace checks passed. No formatter is
  configured. A frontend build was not run because no frontend files changed.

Coverage includes full-detail/reconstructed parity, unchanged source ledger,
repeated numeric-UID occurrences, demand-command sequence preservation, corrupt/
missing/uncommitted/unsupported sources, replay budgets and cancellation, four
publication fault boundaries with temporary cleanup, strict reference admission,
cache isolation/concurrency/versioning, async cancellation, overlapping leases,
queued-release rejection, activation-qualified future coverage, a simulated
90-second lap wait, endpoint authentication/body limits, cached report parity,
and continued journal admission under two occupied analysis slots.

## Reviewed implementation limits

Historical materialization is ephemeral and replays the full committed generation
prefix within fixed budgets. Long/dense recordings may be rejected; this is not
bulk historical backfill or a raw-storage retention policy. Trace availability
preserves context qualifications rather than claiming coaching eligibility.

Reference selection currently supports strictly compatible player practice/
qualifying or Time Trial evidence, not opponent coaching or competitive ranking.
Named-corner mapping and causal driving advice are not introduced. All reports
and answers remain diagnostic-only with coaching/ranking explicitly disabled.
The optional model performs intent routing, not free-form evidence generation.

Ordinary engineer requests retain a 30-second deadline. Explicit future-binding
requests have a reviewed 270-second deadline and at most 240 seconds of waiting;
the original ten-second draft could not cover a real flying lap. Existing 300-
second detail leases are unchanged and never renewed. Cleanup is queued, not
necessarily owner-applied; rejection is surfaced. Clients must allow sufficient
HTTP timeout for deliberate future requests. Future requests without a reference
return a trace-only summary and make no model call; unsupported nonplayer pairs
remain unavailable rather than being silently compared.

Serialized JSON cache budgets are not total heap measurements. Replay timeouts
are cooperative between entries, and running SQLite/decode operations cannot be
preempted. Cancelled historical workers retain admission until they terminate.
No dedicated scheduler, executor or receiver thread was added.

## Remaining operator gates

Linux recovery/load measurements, real-game workload coexistence, optional live
model readiness/evaluation and production deployment were not performed. Synthetic
and unit passing results do not prove zero packet loss or live latency acceptance.
Do not change buffers or relax acceptance thresholds to make those gates pass.
UI decisions remain entirely separate and require explicit user approval.
