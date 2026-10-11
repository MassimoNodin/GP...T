# UI replacement development slices

Planning handoff, October 11, 2026. Implementation has not started. These assignments
do not themselves authorize commits, deployment, service changes, or data deletion.

## Governing requirements

- Reproduce the five-panel mock's layout and visual language, not the old UI.
  Apply [all approved corrections](ui-mock-differences.md).
- The standalone engineer handles in-game speech and spoken questions without a
  website. Drive is radio diagnostics; Review starts with a visual session/lap overview.
- Image labels, values, advice, and features are illustrative. Gate unsupported
  capabilities; never fabricate measurements or conclusions to match the image.
- Reuse verified acquisition/analysis functionality without retaining old navigation.
- Preserve a durable image reference before implementation. Verify desktop fidelity,
  usable smaller viewports, accessibility, and non-happy states.
- Fixture-based visual acceptance and real operational acceptance are separate gates.

## Parallel worker rules

- Use gpt-6.1-sol for this UI parity work, as requested on October 11, 2026.
  Do not substitute Luna or another model. If the dispatcher does not expose
  gpt-6.1-sol, the GPT-6.1 Sol primary performs the work directly.
- Visual acceptance requires reference-aligned typography, geometry, icons,
  colors, and spacing. Do not claim exact parity without rendered comparison.
  Approved semantic corrections remain required.
- Directories below are proposed new write scopes. Each worker owns its directory
  and exact dedicated test paths assigned before dispatch.
- Integrator exclusively owns shared routes, layout, globals, navigation, package
  files, API/types/transport, Python API/CLI registration, migrations, authentication,
  launcher changes, and cross-slice lifecycle/recovery wiring.
- Freeze interfaces first. Request shared-file changes rather than editing concurrently.
- Read applicable AGENTS.md and installed Next.js docs before implementation.
- Workers edit directly with apply_patch and return changed paths, tests/results,
  integration requests, unsupported capabilities, and remaining dependencies.
- Preserve unrelated work. No branches, commits, pushes, deployment, evidence deletion,
  production-budget changes, service startup, or replay transmission without approval.

## Foundation

### F0 - Capability inventory and contract freeze

Owner: integrator. Must precede feature dispatch.

- Inventory v1 imported/recorded and v2 automatic acquisition paths; define verified
  mappings instead of assuming they are interchangeable.
- Freeze runtime health, radio-event, session/lap identity, quality/eligibility,
  comparison, settings, and data-operation contracts, exports and endpoints.
- Define freshness, origin/import/replay, delta baseline/sign, cancellation,
  bounded history/reconnect, errors, fixture schemas, and capability flags.
- Record supported, adaptation-needed, and new capabilities per requirement.
- Assign exact write/test scopes and route parameters before parallel development.

Acceptance: independent workers can implement without assuming live Ask, proactive
radio, standalone audio, or session-wide review questions already exist.

### F1 - Shared visual foundation

Write scope: web/src/features/ui-foundation/ and dedicated tests.

- Build mock-derived tokens, primitives, shell/navigation presentation, distinct
  runtime versus selected-session context, common states, and responsive behaviour.
- Export accessible components/interfaces for page workers; no feature business logic.
- Integrator registers shared styles/layout; workers do not recreate them per page.

Depends on F0. Acceptance: fixture shell matches reference and passes keyboard,
focus, status-state, and narrow-viewport checks.

## Website slices

### U1 - Drive Diagnostics

Write scope: web/src/features/drive-diagnostics/ and dedicated tests.

- Reproduce panel 1: readiness/activity, compact session context, radio history,
  event details, authoritative freshness, and operational diagnostics.
- Include lifecycle outcomes, triggers, evidence limits, timings, suppression/errors,
  history pagination, ordering, and reconnect without duplicated entries.
- No driving charts or browser-owned live microphone/speech.

Depends on F0/F1; production integration requires B1-B3.
Acceptance: radio lifecycle/error fixtures work; browser closure is never a runtime
stop signal; corrections 1-3 and 8-9 are covered.

### U2 - Review Session Library

Write scope: web/src/features/session-library/ and dedicated tests.

- Reproduce panel 2: inventory, metadata, search/filter/order, session opening,
  source distinctions, active/processing/partial/unavailable/empty states.
- Use stable session identity, not raw capture/run IDs, for user selection.
- Consume U6's import interface; do not duplicate import logic.

Depends on F0/F1; production inventory needs B4, import integration needs U6.
Acceptance: accurate discovery/selection and imported-session refresh; complete
does not imply game validity or analysis eligibility.

### U3 - Review Session Overview

Write scope: web/src/features/session-overview/ and dedicated tests.

- Reproduce panel 3: visual lap progression, context, supported stint/event/condition
  information, optional evidence-backed summary, and single/pair lap selection.
- Preserve repeated attempts as distinct identities; explain eligibility and link
  findings to supported evidence. Visuals have accessible equivalents.
- Historical review does not start replay; unknown conditions/stints remain unknown.

Depends on F0/F1 and B4 production evidence.
Acceptance: valid/invalid/partial/repeated-lap selection, explicit delta baselines,
and unsupported event/stint/summary states.

### U4 - Review Lap Analysis

Write scope: web/src/features/lap-analysis/ and dedicated tests.

- Reproduce panel 4: single/pair analysis, linked supported telemetry/circuit/region
  scope, sector differences, observations, and data-quality information.
- Identify target/reference, policy, eligibility, units, sign/baseline, model revision.
- Synchronize scope; reject mismatched evidence and explain missing channels/models.
- Consume U5's frozen question interface. Gate external-tool and unsupported features.

Depends on F0/F1 and B4; U5 integration uses an agreed interface.
Acceptance: linked-selection/identity/compatibility tests and correct target-minus-
reference arithmetic: 1:13.420 versus 1:12.842 is +0.578 seconds.

### U5 - Contextual Review Questions

Write scope: web/src/features/review-engineer/ and dedicated tests.

- Implement panel 4 questions with session/lap/pair/region scope, evidence links,
  limitations, and pending/answered/partial/unsupported/cancelled/failed outcomes.
- Preserve each turn's original scope after selection changes; reject stale responses.
- Optional review audio must not compete unintentionally with standalone live radio.
- Do not implement live orchestration here; unsupported question scopes stay unavailable.

Depends on F0/F1 and B4 scope; adapt recorded Ask only where actually supported.
Acceptance: context/cancellation tests; review work neither starves acquisition nor
interrupts radio. No silent v2-to-v1 identity coercion.

### U6 - Settings and Data Operations

Write scope: web/src/features/settings/ and dedicated tests.

- Reproduce panel 5 and Assistance, Voice & Controls, Connections, Data & Storage,
  Advanced Tools sections, exposing only implemented capability-supported fields.
- Cover configured/applied values, validation, success/failure, restart requirements,
  persistence scope, and sensitive configuration.
- Own reusable import progress/retry/failure/duplicate handling consumed by U2.
- Cover supported export/storage/retention/confirmed deletion and diagnostic capture,
  replay, and track tools. Never create a competing UDP receiver.

Depends on F0/F1; runtime settings need B1-B3, data operations need B4.
Acceptance: apply/restart/failure and deletion-confirmation tests; unsupported fields
gated and diagnostic operations respecting acquisition ownership.

## Backend and standalone-runtime slices

These implement capabilities, not just layouts. UI slices can use explicitly labelled
deterministic fixtures until integration; fixtures never count as production acceptance.

### B1 - Browser-independent Windows voice companion

Write scope: new f1_engineer/companion/ and dedicated tests.

- Own Windows device access, bounded mic capture/audio framing, output, question
  activation, mute/interruption/repeat, device readiness, persistent configuration.
- Freeze authenticated audio transport with B2. Reuse Ubuntu transcription/model
  services; preserve Windows/Ubuntu ownership and avoid browser dependencies.
- Handle device loss/disconnect/recovery independently of dashboard startup.
- Launcher/CLI integration belongs to integrator.

Depends on F0. Acceptance: bounded device mocks, then authorized actual-device
question/response and proactive playback with the website closed.

### B2 - Live questions and proactive radio orchestration

Write scope: new f1_engineer/radio/ and dedicated tests.

- Own authoritative live session binding, supported evidence/question/trigger handling,
  priority, expiry, deduplication, suppression, interruption, cancellation, capability gating.
- Adapt analysis/local_speech.py instead of duplicating transcription. Consume B1
  transport and emit typed events through B3's producer interface.
- Do not take ownership of processing/runtime.py telemetry lifecycle. Protect budgets,
  reject stale results, and never silently retarget changed session/tenure identities.

Depends on F0 and frozen B1/B3 interfaces, which can be fixture-backed during development.
Acceptance: deterministic lifecycle/session-change/trigger and contention tests;
unsupported advice unavailable and delayed messages expire safely.

### B3 - Persistent radio diagnostics and health

Write scope: new f1_engineer/radio_diagnostics/ and dedicated tests.

- Own event store/query, bounded retention, stable identity/order, lifecycle updates,
  session association, timings/reasons/limitations, and health/freshness projections.
- Provide B1/B2 producer interfaces and authenticated bounded history reads.
- No orchestration, shared migrations, implicit raw-audio retention, or confusion
  between radio readiness, telemetry loss, and coaching eligibility.

Depends on F0; develops alongside B1/B2 using producer fixtures.
Acceptance: restart/reconnect persistence, pagination/deduplication, stale/unknown
health, authentication/privacy. Integrator owns schema and route registration.

### B4 - Retained-review and data-operation adapters

Write scope: new f1_engineer/review_contracts/ and dedicated tests.

- Define versioned evidence-oriented mappings over supported automatic/imported
  sessions, not UI-shaped contracts or copies of full telemetry/raw audio.
- Supply inventory/progression, supported stints/events/channels/regions, policy,
  eligibility, model revision, and contextual question identity.
- Preserve provenance/bounded reads; do not silently convert v2 evidence to v1 Ask.
- Specify actual import/export/retention/deletion/replay capabilities and gaps.
- Consume frozen B2/B3 identifiers for radio-linked evidence without blocking basic
  retained-review adapters on complete radio implementation.

Depends on F0. Acceptance: real retained fixtures from both supported evidence paths,
identity/quality/arithmetic tests, rejected incompatible comparisons, explicit gaps.
Existing services, shared transport, API registration and migrations remain integrator-owned.

## Integration and verification

### I1 - Route wiring and cutover

Owner: integrator. Shared-file edits are serialized.

- Install F1/U1-U6 and authenticated B1-B4 adapters. Expose Review, Drive Diagnostics,
  Settings and nested session/lap contexts; define safe old-route redirects.
- Remove browser speech/mic and pinned diagnostic recording/replay dependencies from
  normal live operation. Preserve legitimate import/data/diagnostic capabilities.
- Reconcile styles/interfaces; no unrelated edits or destructive old-code removal
  before replacement acceptance. Update baseline with verified, not anticipated behaviour.

### V1 - End-to-end and visual acceptance

Write scope: new web/scripts/ui-replacement/, dedicated backend integration tests,
and docs/audits/ui-replacement/. Freeze exact test-file ownership during F0.

- Verify five mock compositions, keyboard operation, smaller viewports and all
  loading/empty/stale/disconnect/partial/error states.
- Verify closed-browser live questions/proactive output, interruption/session changes,
  persistent history reconnect, retained review/comparison/Ask, settings and import.
- Check receiver ownership, freshness, readiness, provenance, delta arithmetic, and
  review not disrupting live operation. Screenshots cannot prove hardware behaviour.
- Run focused tests, relevant existing Python/web checks, and production build.
  Report commands/results/screenshots and remaining visual versus operational gaps.
- No production service startup, actual hardware operation, or replay traffic without consent.

## Execution waves

1. F0 freezes capability/contracts; F1 builds shared visual foundation.
2. B1-B4 develop in disjoint scopes against frozen interfaces.
3. U1-U6 develop after F1 using labelled fixtures; U2/U6 import and U4/U5 questions
   use frozen interfaces, not shared editing of each other's internals.
4. I1 wires verified features; unsupported operations remain unavailable.
5. V1 tests complete flows; integrator reviews and hands off remaining gaps.

Dispatch ready slices within available concurrency. Do not delegate an immediate
blocker while integrator waits. No individual slice declares the entire product complete.

## Reusable Sol assignment

> Implement slice <ID> with gpt-6.1-sol only. Read this plan, approved differences, frozen
> contracts, durable mock image, applicable AGENTS.md and installed framework docs.
> Edit directly only in the assigned directory and exact test paths using apply_patch.
> Request shared-file/API/style changes instead of editing them. Preserve unrelated
> work, run focused validation, and report changed paths, results, integration requests,
> capability gaps and dependencies. Do not commit, push, deploy, delete evidence,
> start production services, or treat fixture tests as proof of live operation.
