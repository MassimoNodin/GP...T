# Working engineer v1 capability mapping

Inspected and validated October 12, 2026 on `feat/working-engineer-v1`.
The [acceptance contract](working-engineer-v1-acceptance.md) is authoritative.
This map is an implementation plan, not acceptance evidence for new features.
Partial means reusable components exist, not that the in-game experience passes.
No complete acceptance criterion has been demonstrated on real-game hardware.

## Remaining acceptance work as of October 13, 2026

The original inventory and proposed slices below are historical planning context.
The standalone radio now has recurring reports, basic multi-driver live questions,
adjacent catch/defend watches, observed sector consistency, fixed lap/sector target
pace, backend player-region capture and explicit fixed previous-region diagnostic
comparisons. Dynamic wheel binding, capture/cues, automatic scoped voice question/
task submission and browser-independent radio controls also exist. Automated
coverage does not establish complete acceptance.

Remaining implementation and scope decisions:

- Proactive advice and previous-best/opponent coaching with defensible reference
  eligibility, observed context and explicit missing/material-difference disclosures.
- Focus-corner loss selection or target clarification, supported geometry/reference
  policy, one coaching focus and fixed-reference progress across requested laps.
  Numeric diagnostic differences are not this completed capability.
- Opponent braking/minimum-speed task-owned collection through existing detail
  permissions, aligned player/opponent lap windows and guarded spoken comparisons.
- Confirmed post-pit same-session/same-compound benchmarking, retained eligible
  lap sets, pre-pit-position ranking/tie-breaks, exclusions and one-off reporting.
- Complete session-best/older-lap retrieval beyond the bounded observed history,
  plus partial supported answers when a mixed request contains unsupported facts.
  Current-lap sectors 1/2, fully observed player/opponent last-lap sectors, retained
  eligible numbered laps and labelled same-compound radio-observed best retrieval
  now exist; these are not
  complete historical/session-best coverage.
- Conversation context including prior answers, instructions and tracking results;
  metric/target/task follow-ups with clarification and no cancelled-task revival.
- Real-game acceptance of automatic scoped voice questions/tasks and the explicit
  spoken quiet/cancel-speech/repeat controls, preserving capture/playback echo
  safeguards. These controls now exist, with ambiguous commands clarified rather
  than guessed, but still require the wheel/headset acceptance evidence below.
- Resolve or explicitly agree existing limits: named-driver watches currently
  pause outside meaningful adjacency; target pace uses eligible laps observed by
  the radio connection, not complete historical session-best evidence.

Remaining acceptance evidence:

- Real-game demonstrations of every selected capability with the actual wheel,
  microphone/headset and all browsers closed; startup, recovery and essential
  controls must remain usable without the dashboard or console task submission.
- Integrated pits, reconnects, flashbacks/restarts, missing telemetry/identity
  changes, partial opponent data, task expiry/cancellation and radio priority checks.
- Actual wheel-to-listening, capture-to-answer and event-to-audio latency samples,
  including slow cases and available stage breakdowns, followed by user approval.
  There is no fixed numerical latency acceptance gate.
- Final acceptance record showing successful, failed and pending criteria and
  explicit user acceptance before declaring the branch complete.

## Existing foundations and missing work

### First implemented backend increment

The [live facts/event contract](working-engineer-live-facts.md) now provides
authenticated, freshness-qualified current field timing and same-frame tyre
observations, verified identity bindings and bounded lap/sector/pit events.
Player pit-entry events retain the previous observed field positions for later
benchmark ranking. It uses the existing acquisition/persistence pipeline and
does not depend on detailed trace selection. The original table below records
the pre-implementation inventory; this increment supplies part of slice 1,
not full coaching or standalone acceptance. Focused new checks: 35 passed.

### Standalone radio foundation

The [Windows standalone lap radio](working-engineer-standalone-radio.md) now
consumes the authenticated live endpoint independently of the browser and speaks
new player lap completions or answers a fixed last-lap console command. It adds
a bounded priority/expiry/interrupt scheduler, quiet/cancel/repeat controls and
cross-host freshness handling. Focused radio checks: 56 passed. Installed Windows
voice discovery was exercised; audible playback and live-game operation were not.
This supplies the playback/transport foundation of slice 2, not its wheel,
capture, listening cues or conversational answer requirements. The inventory
table below remains the pre-implementation baseline.

### Dynamic wheel input foundation

The radio now supports optional SDL controller enumeration, press-to-learn
assignment and a saved listening-button binding. Re-learning in a second console
hot-reloads the active radio without code changes or restart. GUID/name matching
survives enumeration reordering and refuses ambiguous devices; reconnect and
held-button baselines prevent unintended activation. A diagnostic-only `test`
command works without the backend/browser/audio. Native enumeration succeeded
on Windows but no controller was connected. This remains an input request hook:
the radio explicitly reports capture unavailable, not listening. MOZA-focused
activation, microphone capture and cues still require implementation/validation.
Final focused validation: 49 input cases, 114 including radio/CLI checks. Full
regression: 1,863 passed, 16 skipped; six final test-only cases were additionally
verified in the focused run. Existing dependency pins remain unchanged; SDL is
an optional local `radio` extra, not a production deployment change.

### Standalone listening and transcription increment

The saved wheel button and console controls now start/finish bounded microphone
capture with distinct start/end tones and quieter ongoing cues. A dedicated
worker keeps native audio and correlated authenticated transcription off the
radio loop. Capture/transcription cancellation, session and wheel-instance
invalidation, safety timeouts and playback suspension guard the lifecycle.
The `radio --capture-test` path works without backend/token/browser and keeps
audio in memory without upload. Normal mode produces an unverified transcript
draft through the existing pinned speech runtime, not an automatically submitted
engineer question. Periodic cue guards omit brief audio intervals; hardware
isolation and usability must be checked before acceptance.

The actual R3 was detected and button 32 learned after the input increment; the
user deferred game-focused validation to the full test. Audio-device enumeration
also succeeded, but neither native recording nor cue playback was initiated.
Slice 2 still needs actual headset/game validation and spoken question/answer
integration; no acceptance criterion is marked complete by this increment.
Validation: 57 new listening tests; 221 focused checks passed; full regression
1,926 passed, 16 skipped with two existing deprecation warnings. Native audio
enumeration, compilation and CLI help passed; actual recording/cues/transcription
and hardware latency remain pending. Existing dependencies/runtime pins were
preserved; only optional local audio dependencies were added.

| Requirement | Current foundation | Gap to acceptance |
| --- | --- | --- |
| Proactive comparisons | `engineer_activity/calls.py` observes published player laps, logs best-lap deltas, invalid/pit laps and telemetry state changes. | Calls are activity records, not a standalone spoken coach. Add opponent/comparable-reference selection and event-to-radio delivery. Its session best is not a condition-matched coaching reference. |
| Custom recurring reports | Backend preferences filter topics and shape answer wording. | No natural-language report configuration, recurring trigger/task lifecycle or lap-boundary gap snapshot reports. |
| Multi-driver, multi-fact questions | Attempt summaries, participant identities, lap inventories and session-history timing exist. | Current ask routes require selected attempts/pairs; add driver resolution, live timing retrieval and a multi-part query plan with partial results. |
| Bounded tracking | `session_engineer.py` supports future bindings and request-owned detail leases. `detail_policy.py` admits explicit targets. Aligned braking/minimum-speed measurements exist. | Future bindings are not a conversational task manager. Add acknowledged task boundaries, driver identity binding, duration, results, cancellation and expiry. |
| Focus-corner coach | Corner loss candidates, region comparisons and driving-pattern assessments exist. | Existing corner ranking is tightly gated, including time-trial restrictions. Add supported race/reference eligibility and fixed-reference multi-lap coaching, with numeric-region fallback or clarification when named corners are unavailable. |
| Catch-or-defend watch | UDP lap data includes positions, front/leader gaps and total distance. Detail policy chooses race neighbours. | Add driver-specific timestamped gap history, stable identity, trend calculation, pit/lapping discontinuity handling and alert cooldowns. An arbitrary opponent gap is not always a directly supplied field. |
| Consistency coach | `observation_set.py` aggregates selected-window measurements and onset spread across bounded attempt sets. | Add spoken task selection, eligible rolling lap sets, race-safe context and follow-up reporting; do not present the existing mode-gated analysis as general race coaching. |
| Target pace | Best/reference services, lap/sector timing and distance deltas exist. | Add target parsing, fixed reference ownership, live sector/lap milestones and deficit reports. Existing best-selection policies must not be silently broadened to race. |
| Conversational follow-ups | Activity store and ask log retain questions/answers; model routing exists. | Logging is not model memory. Router messages contain the current question and capability selection, not conversation history. Add structured context, reference resolution, bounded history and cancellation-aware state. |
| Post-pit compound benchmark | Session history supplies tyre stints. `processing/lap_enrichment.py` associates laps with tyre context and builds stint views. | Add pit-entry position snapshots, tyre-change confirmation, eligible opponent stint averages, pre-pit-position ranking and one-shot radio reports. Existing stint views do not implement this ranking. |
| MOZA R3 listening | Backend transcription accepts bounded audio clips. | No wheel input binding, standalone capture or listening-cue subsystem found in inspected source. Implement and verify on the actual wheel with the game focused. |
| Browser-independent operation | Acquisition/API, evidence storage, model routing and transcription run outside the browser. | Spoken playback uses browser speech synthesis. Companion launcher sets up SSH/dashboard, not a standalone radio. Add Windows capture/playback, lifecycle, essential settings/status and browser-free submission. |
| Honest data | Readiness, provenance, validity, coverage and policy gates already abstain on unsupported evidence. | Carry these constraints through live facts/tasks/speech; never bypass race diagnostic restrictions merely to obtain an answer. |
| Radio discipline | Activity records have priority/deduplication. Browser playback can replace/cancel speech. | No shared standalone scheduler for answers/proactive calls, expiry, interruption, quiet/cancel/repeat or stale-backlog prevention. Preference values alone do not establish this behaviour. |
| Task visibility | Detail demands have ownership and cleanup; activity is inspectable. | Add semantic active-task registry and spoken list/cancel-all, including invalidation of queued outputs. |
| Session correctness | Participant tenure, lifecycle epochs, continuity, supersession and future-binding checks exist. | Propagate lifecycle invalidation through conversation, tasks, pit snapshots, references, gap histories and radio queues. |
| Latency and live acceptance | Receiver/publication diagnostics and deterministic tests exist. Migration baseline explicitly records pending live acceptance. | Add correlated timing across wheel/capture/transcription/reasoning/playback; report results without hard latency gates. Real-game browser-free demonstrations and user acceptance remain required. |

Paths in the table are relative to `f1_engineer/` unless stated otherwise.
Browser playback is in `web/src/lib/local-speech-controller.ts`. Current ask
contracts are in `analysis/engineer_runtime.py`, `analysis/engineer_ask.py`,
`analysis/engineer_ask_service.py` and `analysis/session_engineer.py`.

## Data constraints to preserve

- Decode support is not proof a field is present, fresh, retained or reliable
  for every opponent. Timing inventory and detailed driving traces are distinct.
- Reuse live/session evidence instead of introducing a competing UDP receiver.
  Retain cheap timing/context for the field; request bounded high-rate detail
  for tracked drivers using the existing demand mechanism.
- Bind driver references to verified participant tenure and lifecycle epoch,
  not a mutable car index or display name alone. Ask when a name is ambiguous.
- Gap trend calculations must state how the interval was obtained; front-car
  delta cannot simply be treated as delta to any named driver. Missing coherent
  observations must cause abstention rather than fabricated interval arithmetic.
- Opponent history may be incomplete. Post-pit averages need per-lap eligibility,
  known compound, pit/validity exclusions and disclosed sample count. Confirm
  live retained enrichment coverage before treating historical views as a live
  benchmark source. Do not reconstruct an unobserved pre-pit position.
- Named-corner coaching requires supported geometry/model evidence. Numeric
  region measurements and time-trial ranking are not interchangeable with
  race coaching. Add explicit policies and tests rather than removing gates.

## Proposed implementation slices

Each slice is a separate logical commit after focused validation, then pushed.
These are proposed work boundaries, not promises that each is a single commit.

1. **Live fact and event contract:** Expose coherent session/epoch/driver-bound
   timing, gaps, pit/compound and lap/sector milestones with observation time,
   availability and provenance. Test stale/missing data, rewinds and identity
   changes. This is the immediate backend prerequisite for queries and reports.
2. **Standalone radio vertical slice:** Add a Windows non-browser client with
   configurable input/capture/playback adapters and status/settings controls.
   Use authenticated backend access through the existing split-host transport.
   Demonstrate wheel activation, listening cues, one simple spoken answer and
   one proactive lap call. Add the shared priority/expiry/interrupt scheduler
   here so later features cannot create competing speech pipelines.
3. **Conversation and multi-fact retrieval:** Add a typed request plan, driver
   resolution, partial-result answers and bounded conversation context. Keep
   measurement logic deterministic; requests/history are not permission to
   change telemetry facts. Test follow-ups and ambiguous/missing targets.
4. **Task registry and recurring reports:** Add create/update/cancel/list/expire,
   fixed references and detail-demand ownership. Implement requested lap reports
   and next-lap braking/minimum-speed tracking. Invalidate pending radio output
   when tasks or session bindings change.
5. **Coaching modules:** Add catch/defend trends and target pace, then consistency
   and focus-corner coaching using explicit race-safe reference policies.
   Verify fixed references, sufficient samples and unavailable-data responses.
6. **Post-pit benchmark:** Capture pre-pit positions, detect confirmed compound
   changes, filter same-session same-compound samples and rank eligible drivers.
   Test tied positions, nearest-driver fallback, one-lap samples, missing tyre
   history, repeated packets, flashbacks and stops without a tyre change.
7. **Real-game acceptance and latency report:** Run failure/reconnect exercises
   and demonstrate every accepted ability on the actual split-host/wheel/audio
   setup with browsers closed. Present latency distributions and slow cases;
   the user chooses acceptance or optimisation. Branch completion remains gated
   on functionality and user assessment, not a numerical latency target.

Slices 1 and 2 establish the first usable in-game path; later slices extend it.
No model, speech-runtime or deployment substitution is authorised merely by this
map. Existing runtime pins and production data must remain intact.

## Validation of the mapping baseline

Two focused suites ran locally using `uv run --extra app --extra dev pytest`:

- Engineer activity/preferences/query, session engineer, race engineer integration,
  session comparison policy and local speech: **162 passed**.
- Session reference/best, corner loss, observation sets, car lap inventory,
  session history/import, detail policy/demands and lap enrichment: **313 passed**.
- Both runs emitted the existing Starlette/httpx deprecation warning.

These 475 passing tests substantiate reusable foundations only. The full suite,
browser checks, live game, MOZA wheel, microphone and audible output were not
validated in this mapping task. No production service or runtime was changed.
