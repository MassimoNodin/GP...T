# Parallel UI and data work plan

**Status:** accepted working arrangement  
**Date:** 2026-10-05  
**Baseline:** `28ad546` (`main` at the time the UI worktree was created)

This arrangement lets a UI agent make progress on the eight mock screens while the original agent continues telemetry, storage, analysis, and API work. It follows [Decision 0061](architecture.md#decision-0061-deliver-the-mock-product-through-evidence-backed-web-capabilities) and [the product implementation plan](implementation-plan.md).

## Work lanes

| Lane | Owner | Scope |
|---|---|---|
| Web presentation | UI agent in its own worktree | Reference-matched visual shell/navigation, design tokens, reusable cards/tables/charts/status elements, page composition, responsive and keyboard behavior, and presentation-only extraction into new components. Use existing services and contracts. |
| Evidence and services | Original project agent in the primary checkout | Python capture/decode/import/storage/analysis, database migrations, eligibility/lifecycle, API contracts and routes, selected-identity/provenance validators, and service-side validation. Keep its current implementation sequence and commits. |
| Integration | Primary agent coordinating both lanes | Contract ledger, assign one editor for shared files, merge reviewed commits, resolve cross-lane behavior, run combined acceptance, and keep the active original checkout intact until handoff is safe. |

## File ownership

- The UI agent owns `web/src/app/globals.css`, the shared visual shell/layout, and new presentational components. It may add visual-only components beside routes, but must avoid changing telemetry types, API clients, proxy routes, query/selection parsing, and policy logic.
- The original agent owns `f1_engineer/**`, `tests/**`, `web/src/lib/api.ts`, navigation/selection/provenance helpers, `web/src/app/api/**`, and route data-loading/control behavior.
- Existing route page files often combine rendering, API loading, query parsing, polling, and provenance validation. Treat `web/src/app/page.tsx` and the current `/live`, `/compare`, `/engineer`, `/recordings`, `/sessions`, `/settings`, and `/track` route files as coordinated files: UI work should extract and style presentation components; the original agent retains data requests, operation controls, selection, and response checks. If a route file itself needs edits from both lanes, stop and agree on a small extraction/contract commit first.
- `docs/implementation-plan.md`, package manifests, generated API types, and shared CSS/component interfaces are coordinated files. Assign one editor per change and announce contract changes before implementation.

The UI uses current committed API v1 contracts. A missing capability renders a truthful unavailable/diagnostic state. For visual work without a running local API, use isolated, clearly labelled development fixtures that cannot enter production responses, capture archives, eligibility registries, or persisted data. The UI never calculates reference, ranking, lifecycle, or coaching eligibility.

## Git and integration

1. Keep the original agent's primary checkout and branch under its control. Do not switch branches, stash, reset, stage, or rebase its in-progress files.
2. The UI lane uses the managed worktree `C:\Users\massi\.codex\worktrees\ui-mock-parity\F1-Engineer`, created from pinned commit `28ad546`. Its branch is isolated from the original checkout. If the original advances, UI work can continue against the pinned v1 contracts; later integration brings selected commits together.
3. Keep UI commits cohesive by shell or screen. The data lane continues to commit its own service/API changes on the primary branch. Avoid direct pushes to `main` from the UI lane.
4. Integrate reviewed slices in a separate integration worktree based on the chosen common ancestor. Merge each lane's commits with history preserved; do not mix merge and cherry-pick for the same commits. Resolve route conflicts manually and re-run the cross-boundary acceptance there.
5. Update the original checkout only after the original agent's current turn is settled and the user-visible worktree handoff is deliberate. Never force-update a branch beneath an active agent.

## Shared contract and acceptance rules

Before a UI panel consumes a new field or action, record its endpoint/type, exact selection identity, unit, freshness/coverage, provenance checks, null/unavailable meaning, limitation text, and acceptance check. Preserve the API envelope, protected control token, explicit run/session/attempt/reference/model/region identities, bounded navigation state, pinned recording/replay operation IDs, and distinct supported/missing/unsupported/permission-denied/service-unavailable states.

Data contracts are additive where possible. UI accepts absent optional fields and displays them as unavailable. Breaking contract changes require agreement from both lane owners and an architecture decision before either side implements against the new shape. The backend owns semantic validation and policy; the UI owns presentation and interaction.

Integrate after the common shell and each complete screen slice, and after any contract change. For every merged slice, verify reference-viewport appearance, navigation-state preservation, keyboard and narrow-screen behavior, realistic loading/empty/stale/error/unavailable states, and response identity. Full acceptance retains the end-to-end path: capture → import → session/run → attempt selection → comparison/engineer evidence → replay.

## Work that can start now

All eight routes exist at the pinned baseline. The UI agent can begin the shared shell and high-fidelity presentation on current payloads, prioritizing Dashboard, Recordings, Sessions, Live, Compare, Track, Engineer, then Settings. Existing evidence supports recording/import/replay, session and attempt browsing, selected comparison, observed trajectory, diagnostic regions, recorded-evidence engineer summaries, and local read-aloud. Missing information should keep its intended panel position with an honest state rather than hold the whole screen or be filled with fake live values.

The data lane can continue services independently. Pause/resume recording groups, replay seeking and video synchronization, storage/archive actions, additional live packet groups, stable opponent identity, calibrated circuit geometry, strategy/coaching, free-text AI, and speech input are still dependencies; expose these states as unavailable until the data lane closes their contracts and acceptance gates.

## Risks and controls

- **Route conflicts:** maintain the file ownership above; extract presentation from route containers before concurrent edits.
- **Stale or changing contracts:** publish additive shapes and sample responses before a panel depends on them; pin identity in UI state and validate returned identity.
- **Local resource contention:** do not start a second API/database/UDP recorder accidentally. Use isolated fixtures or unique API, database, recording, and token paths for independent previews; keep the existing import/record/replay reservation unchanged.
- **Misleading screenshots:** visual fixtures are visibly development-only. Unknown units, stale values, uncalibrated maps, sparse damage, and ineligible references retain warnings/unavailable states.
- **CSS regressions:** the UI lane owns shared design tokens and CSS and checks all eight routes at desktop and narrow widths after global style changes.
