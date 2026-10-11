# Backend-only selective processing: agent slices

Prepared October 11, 2026. Implementation subsequently authorized by the user;
UI changes and production deployment remain excluded. Frozen implementation
contracts are in `backend-contract-v1.md` and operator behavior is documented in
`backend-services.md`.

## Scope and baseline

This outline assumes the intended next-stage work is the selective-processing roadmap in `selective-processing-implementation-plan.md`: historical detail, reference selection/cache, corner analysis, and AI integration. Confirm this assumption before assignment if a different proposal was intended.

Gate A already provides opt-in live detail selection and explicit task demands. Use `selective-processing-review.md` for corrections to the original handoff. Historical materialization and AI task integration are not Gate A capabilities. Reuse existing analysis services rather than build a second engine.

The backend flow is: raw journal and whole-field inventory -> explicit live demand OR bounded historical materialization -> eligible evidence -> reference selection/cache -> deterministic comparison and supported corner measurements -> an evidence-grounded answer on explicit request.

### Explicit exclusions

- No edits under `web/`: components, layout, styling, routes, controls, browser state, navigation, and voice UX remain deferred.
- No UI design commitment. Each capability is testable through Python/API calls.
- No receiver/buffer tuning, relaxed loss thresholds, changed default profiles, production deployment, automatic replay/import, or packet-triggered model calls.
- No rewriting published evidence or retained reports, guessing missing data, silently retargeting drivers, or counting deliberate deferral as packet loss.
- Preserve the dirty working tree, including existing UI and UDP-replay work. Excluding UI from this plan does not authorize reverting it.
- No branches, commits, pushes, or PRs without separate user authorization.

## Assignment rules

Assign one slice per agent. Inspect applicable `AGENTS.md`, baseline diffs, and actual interfaces first. Suggested touchpoints below are not blanket permission to rewrite those files. The coordinator approves exact write sets before implementation.

Use isolated workspaces or disjoint write sets. Only Slice 6's integration owner edits shared API/runtime/coordinator/CLI wiring. Other agents return required adapter contracts instead of independently editing shared files. If Luna agents are assigned, use `gpt-6-luna`; other agents need not use that model.

Preserve logical-session occurrence, epoch, driver tenure, attempt revision, and source identity. Current live bindings are not historical identity. Availability, game validity, acquisition continuity, and coaching eligibility are distinct. A successful comparison is not proof of an eligible coaching reference.

## Sequence and dependencies

| Slice | Deliverable | Depends on | Scheduling |
| --- | --- | --- | --- |
| 0 | Shared contracts and eligibility policy | None | Coordinator first |
| 1 | Historical detail materialization | 0 | Parallel with 2 |
| 2 | Deterministic reference selection | 0 | Parallel with 1 |
| 3 | Bounded reference/measurement cache | 0, 2 | Parallel with 4 |
| 4 | Evidence-backed corner analysis | 0, 2 | Cache optional, not required |
| 5 | Explicit AI detail-demand orchestration | 0, 1, 2, 4 | Develop against contracts; integrate afterward |
| 6 | Backend wiring, recovery and load acceptance | 1-5 | Single integration owner |

## Slice 0 — Lock shared contracts

**Goal:** settle cross-agent decisions before implementation.

**Deliverable:** a versioned contract supplement for evidence identity, materialization requests/results, selection policy, cache keys, analysis outputs, and engineer requests. Define service job states, stable failure reasons, retryability, cancellation, and publication visibility. New job states must not replace Gate A's existing detail-task state machine.

Lock numeric budgets for concurrency, queue depth, source bytes, decoded rows, execution time, cache entries/bytes, request lifetime, and derived storage. Decide durable versus ephemeral materialization and failure-safe publication. Agents must not invent these defaults or a new worker architecture.

Define diagnostic versus reference/coaching admission using existing context and quality policies. No eligible reference is a normal result, not fallback to an invalid or incompatible fast lap.

**Inspect:** `processing/evidence.py`, `analysis/session_comparison.py`, `analysis/reference_selection.py`, `analysis/ai_admission.py`, and API schemas (paths relative to `f1_engineer/`). Own a new contract document; no runtime changes in this slice.

**Acceptance:** examples cover complete evidence, deferred detail, acquisition gaps, stale tenures, repeated session UIDs, and no eligible reference. Coordinator approves interfaces, budgets and subsequent agents' write sets.

## Slice 1 — Materialize historical detail on demand

**Goal:** reconstruct a specifically requested historical trace without returning all-car detail processing to the live hot path.

**Deliverable:** a bounded service for one historical attempt and tenure. Reuse decoding, assembly and lap-boundary rules. Read sufficient preceding context to establish identity and a genuine start; never use the current roster or stitch partial selection periods into a complete lap.

Create separate derived evidence with immutable source ranges/hashes, materializer version, coverage and qualifications. Keep the original deferred attempt and saved reports unchanged. Duplicate requests are idempotent under the approved storage contract. Missing/corrupt input, gaps, uncertain boundaries and budget exhaustion produce explicit qualified or unavailable results.

**Ownership:** a new service under `f1_engineer/processing/` and focused tests. Shared evidence/journal adapters go through Slice 6 after contract review.

**Acceptance:** parity with full-detail processing on a fixed lossless fixture; repeated UID/tenure replacement; missing start; gaps; truncated/corrupt journal; cancellation; duplicate/concurrent jobs; budget exhaustion; publication crashes. Prove original evidence and ingestion configuration remain unchanged.

## Slice 2 — Select an eligible reference deterministically

**Goal:** choose an explainable reference, not merely the lowest lap time.

**Deliverable:** extend/adapt existing reference policy to the approved evidence generation. Apply compatibility and quality filters, deterministic ranking/tie-breaking, and return revision, provenance, policy version and exclusion reasons. An explicit reference is validated, never silently replaced.

Candidates needing detail may be reported, but selection cannot silently launch unbounded materialization or treat metadata as a complete trace.

**Ownership:** focused changes to `f1_engineer/analysis/reference_selection.py`, a dedicated adapter if needed, and policy tests. No API wiring or unrelated analysis refactor.

**Acceptance:** invalid/partial/pit/gap-qualified laps, incompatible contexts, equal/missing times, rejected explicit references, reproducible ordering, and empty eligible sets. Test diagnostic and coaching admission separately.

## Slice 3 — Cache references and measurements within budgets

**Goal:** reduce repeated decoding/measurement without hiding stale evidence.

**Deliverable:** a bounded cache keyed by immutable revisions/content hashes, evidence generation, policy/analysis versions and measurement parameters. Selection results also track candidate-set changes. Apply approved eviction, byte/entry limits and concurrency behavior; cache bypass preserves identical semantics.

Coalesce duplicate work safely. Failure/cancellation cannot leave stuck entries. Never share the ingestion owner's SQLite connection across threads or treat model output as cached evidence.

**Ownership:** a new cache module under `f1_engineer/analysis/` and tests; return integration adapters without independently editing Slice 2's files.

**Acceptance:** hit/miss equivalence, version isolation, candidate-set updates, bounded memory, eviction, concurrent duplicates, cancellation and invalid entries. Report measured performance, not promised speedups or zero live drops.

## Slice 4 — Produce supported corner measurements

**Goal:** return structured driving differences grounded in eligible traces.

**Deliverable:** a narrow service adapting existing comparison/region/corner analysis to the approved evidence interface. Include measured deltas, supported braking/throttle/line observations, source revisions, distance intervals, coverage and limitations. Keep measurements separate from interpretations.

Use named corners only when supported by existing track mapping; otherwise use numeric distance regions. No unsupported causal instructions, guaranteed gains, or setup recommendations inferred from simple correlation.

**Ownership:** a new analysis adapter and tests, reusing `session_comparison.py`, `corner_comparison_brief.py`, `corner_loss_candidates.py` and `paired_region_service.py` under `f1_engineer/analysis/`. Approve exact changes to existing modules beforehand.

**Acceptance:** fixed-trace reproducibility, distance/coverage gaps, unsupported tracks, missing channels, reference rejection, and provenance. Insufficient evidence returns limitations/refusal rather than invented coaching.

## Slice 5 — Connect explicit engineer requests to detail demands

**Goal:** acquire only the evidence needed for an explicit backend request.

**Deliverable:** an orchestration adapter around existing ask/admission services. Resolve concrete identity first. Reuse complete evidence, request Slice 1 for authorized historical detail, or acquire future live detail through Gate A's owner-applied task leases. These are distinct paths, not interchangeable fallbacks.

Bound waiting, renewals and retries. Release owned leases on completion/error/cancellation without releasing another request's overlapping lease. Queued is not active; activation is not complete-lap coverage. Stale targets fail without retargeting. The model does not choose raw journal ranges, change processing configuration, or decide evidence eligibility.

Only admitted structured analysis reaches the model. Return pending/unavailable results where appropriate. No automatic coaching loop, packet-driven prompts, dashboard-refresh calls, or new speech behavior.

**Ownership:** a new orchestration adapter and tests, inspecting `engineer_ask_service.py`, `ai_admission.py`, `engineer_query.py` under `f1_engineer/analysis/` and `f1_engineer/processing/detail_demand.py`. Shared wiring belongs to Slice 6.

**Acceptance:** fake model/clock cases for available, deferred, waiting, expired, stale, cancelled and unavailable requests; overlap-safe cleanup; bounded retries; no model invocation before admission; source-qualified answers; continued ingestion while waiting. Real-model evaluation remains a separate operator gate.

## Slice 6 — Integrate and validate the backend

**Goal:** deliver a coherent backend without frontend changes.

**Deliverable:** single-owner wiring with necessary authenticated API endpoints and approved schemas, preserving existing behavior/defaults. Expose copied bounded job/cache/demand diagnostics without secrets. Distinguish intentional detail suppression from acquisition loss.

Own persistence adapters, recovery, publication visibility, error mapping, shutdown/cancellation and backend operator docs. Heavy replay/analysis cannot block the ingestion owner or bypass admission limits. New scheduling/worker architecture requires explicit review, not an improvised integration fix.

**Ownership:** focused changes as needed to `api/app.py`, `processing/runtime.py`, `processing/coordinator.py`, `processing/evidence.py` and `cli.py` under `f1_engineer/`, plus integration tests and backend docs. No `web/` edits.

**Acceptance:** end-to-end materialization/reference/measurement/answer; auth and invalid inputs; concurrent-work limits; restart and publication faults; shutdown; unchanged full-profile behavior; repeated UID occurrences; real loss versus deferral. Use separate disposable evidence for load tests, not production data.

## Validation and agent handoff

1. Record baseline edits and approved ownership. Flag interface mismatches before editing another slice's files.
2. Run focused new tests and relevant regressions with fixed clocks and mocked model calls where appropriate. Report exact commands, counts, skips and failures; earlier documented results are not current validation.
3. Integration owner runs `uv run --extra dev --extra app pytest`, focused `git diff --check`, and reviews the complete task diff. This documentation-only outline itself requires no runtime tests or frontend build.
4. Return changed paths, contract decisions, tests, resource measurements, unresolved risks and required shared adapters. Distinguish implemented, tested, operator-accepted and deployed states.

Linux recovery/load checks and an explicitly authorized live coexistence run remain release gates. Synthetic replay cannot certify the game's sender or recover packets absent from the capture. Do not relax loss/latency thresholds to pass.

## Copyable agent assignment

> Implement Slice <number> from `docs/backend-agent-slices.md` using the approved Slice 0 contract and write set: <paths>. Inspect repository instructions and baseline diffs first. Preserve unrelated work. Do not edit `web/`, another slice's files, or production settings. Do not branch, commit, push, deploy or delegate without separate authorization. Build the bounded service and focused tests; return shared adapters to the integration owner. Report changed paths, validation, limits and unresolved decisions. If interfaces or scope conflict, stop that part and report the concrete blocker.

## UI decision deferred

After backend acceptance, the user decides whether and how to expose these capabilities. No slice assumes a reference picker, task panel, coaching dashboard, refresh model or voice interaction. UI work needs a separate proposal and explicit approval; it is not a hidden final step of this plan.
