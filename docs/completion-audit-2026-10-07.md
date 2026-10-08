# Completion and workload audit - 2026-10-07

## Assessment

Three requested `gpt-6-luna` reviewers examined backend, frontend and release completion. A specialist Sol subagent reviewed architecture and release boundaries. Reviews were read-only; only this audit document is added.

The evidence foundation is substantial, but the presentation is not launch-ready. Dashboard, Sessions, Engineer, Recordings and Settings prominently mount reference compositions with fixture values and disconnected controls. Real workflows sit underneath in disclosures. Passing tests does not establish real user-journey acceptance.

## Verified baseline

- Python: 901 passed, 10 skipped, one warning in 61.39 seconds (`.venv/Scripts/python.exe -m pytest -q`).
- Next.js production build, TypeScript validation and page generation passed (`npm run build`).
- All 26 existing web `test:*` scripts passed.
- Warning: deprecated FastAPI/Starlette TestClient `httpx` integration, not a failing application test.
- No real-game, physical microphone, browser-interaction or clean-machine acceptance was performed. Platform-specific skipped checks still need coverage accounting.

## Accepted audit decisions

1. Retain Python evidence services, protected local API and Next.js. Passing baseline and implemented services justify integration work, not a rewrite.
2. Require truthful production surfaces before new capability. Operational-looking examples must become real evidence or explicit unavailable states; opening real controls underneath is insufficient.
3. Preserve deterministic evidence, explicit selection and isolated synthetic mode. Missing evidence must not produce latest-lap substitutions or speculative coaching.
4. Preserve local, single-owner operation, loopback API binding and server-held tokens. Remote hosting/multi-user use requires separate security review.

Recommended release scope, subject to product approval: local capture/import, explicit session/lap selection, supported deterministic comparison, capture replay and optional local-model questions. This does not replace the broader eight-screen plan or declare its unfinished stages complete.

## Immediate workload

Engineering-day estimates are planning ranges, not commitments. Packages overlap; do not sum them blindly.

| Priority | Work | Evidence and acceptance | Estimate |
| --- | --- | --- | --- |
| P0 | Make real workflows primary on five routes | Remove production operational fixtures; isolate labelled reference/test examples. `web/src/app/page.tsx:731`, `web/src/app/sessions/page.tsx:22`, `web/src/app/engineer/page.tsx:275`, `web/src/app/recordings/page.tsx:69`, `web/src/app/settings/page.tsx:40` | 2-5 days for primary-workflow migration; 5-15 for deeper layout integration |
| P0 | Fix storage/status claims | Measured usage or unavailable; no invented quota; qualified privacy/runtime wording. `web/src/app/AppHeader.tsx:202`, `web/src/app/AppHeader.tsx:211`, `docs/usage.md:84` | 0.5-1 day, overlapping UI |
| P1 | Check visible primary controls | Actions work, are disabled with reasons, or are removed. `web/src/app/ReferenceScreens.tsx:784`, `web/src/app/ReferenceScreens.tsx:1463`, `web/src/app/ReferenceScreens.tsx:1573`, `web/src/app/ReferenceScreens.tsx:2127` | 1-3 days if reference surfaces remain |
| P1 | Real-service end-to-end acceptance | Capture -> import -> selected session/attempt -> comparison/Engineer evidence -> replay; stale/empty/invalid/interrupted/corrupt paths. `docs/implementation-plan.md:76` | 2-5 days after UI correction; capture/game access needed |
| P1 | Browser/accessibility coverage | Navigation/forms, identity continuity, keyboard, errors/loading and supported narrow viewports. Existing checks are library-oriented (`web/package.json:5`) | 2-4 days, overlapping acceptance |
| P1 | Clean Windows/model setup | Install/start/stop and digest/status agree; grounded Ask; core app works without model (`docs/usage.md:84`) | 2-5 days, overlapping acceptance |
| P1 if speech ships | Physical microphone acceptance | Permissions, record/stop/cancel, transcription/edit/explicit draft use; no auto-send; typed fallback (`docs/implementation-plan.md:26`) | 0.5-2 days; hardware needed |
| P1 | Release checks/distribution docs | Repeatable Python/web checks, platform-skip accounting and clean-install/source-distribution docs. No repository `.github` directory exists at audit time | 2-5 days; installer separate |

## Broader product backlog

These are not automatic narrow-MVP blockers; they become mandatory when advertised as release capabilities.

- Writable service Settings: protected validated changes, effective/pending/restart state and settled recording; root migration separately reviewed. Estimate 5-15 days (`docs/implementation-plan.md:66`).
- Safe retention/deletion: define catalog/import/run references, protect active/referenced artifacts and handle interruptions. Estimate 5-10 days (`docs/implementation-plan.md:67`).
- Replay seek: deterministic reconstruction/checkpoints; preserve timestamps, identity and source immutability. Packet Step is not seek. Estimate 10-25 days (`docs/implementation-plan.md:84`).
- Real 2026 validation: complete representative captures and count/attempt/trace/context reconciliation. Estimate 1-3 days once data exists plus discovered fixes; eligibility remains conservative (`docs/telemetry-protocol.md:93`).
- Additional packet families: explicit product need, pinned layouts and format/version/size guards. Estimate 2-5 days per family plus integration. Opaque preservation is intentional (`docs/telemetry-protocol.md:93`).
- Full eight-screen parity: estimate screen by screen after migration decision; overlaps immediate UI work (`docs/implementation-plan.md:89`).
- Native packaging/overlay/global input, synchronized video, named opponents, unrestricted Race comparison, verified geometry, ideal lines and strategy/tyre/live coaching remain separate expansions with evidence gates (`docs/implementation-plan.md:104`).

## Proposed Luna implementation waves

Assessment workers completed; these are future assignments, not active edits.

1. Decide reference-layout migration versus making current real workflows primary. Keep architecture/security decisions with Sol.
2. Parallel Luna scopes: Dashboard/Sessions; Recordings/Engineer; Settings/shared-header. Explicitly assign shared components/CSS to avoid conflicts and preserve selection/provenance guards.
3. After route contracts settle, delegate browser checks and release scripts/docs separately; parent integrates and reruns baseline.
4. Exercise real F1 acquisition and microphone. Agents can prepare procedures, not certify unexercised hardware.
5. Separately scope Settings writes, retention or seek after prioritization and specialist review.

## Release gates

Truthful primary screens; no operational fixtures/inert controls; real capture-to-replay acceptance; understandable exclusivity/queue/retry/duplicate/restart behavior; safe unauthorized/stale/corrupt paths; core operation without model/microphone; actual optional-runtime/device acceptance; keyboard/viewport checks; repeatable installation/release checks with platform-skip disposition; accurate F1 25/2026/coaching claims.

No reliable percent-complete figure exists: the narrow evidence MVP and full mock-product roadmap have different definitions of done. No feature implementation or deployment was performed in this assessment.

## First implementation batch

The assessment above is a historical snapshot. This batch makes the existing real Dashboard, Sessions, Engineer, Recordings and Settings workflows primary, removes their production reference compositions and outer closed disclosures, and preserves workflow fragment IDs and synthetic-mode early returns.

Accepted specialist-reviewed decision: reuse existing evidence components, not another production data path. Preserve exact-selection guards, protected controllers and synthetic isolation. Upstream header recording/storage evidence is retained; fake desktop window controls are removed and privacy wording describes local services rather than universal network privacy.

Production-workflow source/AST checks complement existing header-rendering and transport tests. Upstream header, transport and reference-extraction tests now have named package scripts. These checks do not certify real-game capture, microphone use or full browser acceptance. Release automation and rendered-browser regression work remain unfinished after an agent subscription-sharing usage limit interrupted that work.

Final integrated validation on October 8, 2026: 917 Python tests passed, 10 skipped, one existing TestClient deprecation warning; all 30 named frontend check scripts passed, including the corrected header-rendering check; production build and TypeScript passed. The Python skips concern Windows symlink privileges or platform-specific POSIX/Windows filesystem behavior, not missing app dependencies. Complementary POSIX execution and privileged symlink coverage remain release-validation work.
