# Working engineer v1 acceptance criteria

Agreed October 12, 2026 for `feat/working-engineer-v1`. These are pending
acceptance requirements, not implemented capabilities. Completion requires a
proactive spoken in-game engineer, not just a dashboard or successful replay.

## Engineer abilities

1. **Proactive coaching:** Speak event-triggered comparisons with opponents and
   previous best comparable attempts. Identify references, align track regions,
   and distinguish measurements from suggestions. Consider lap validity,
   circuit/layout, conditions, tyres, fuel and traffic where observed; disclose
   missing context and material differences. Do not imply proven causation.
2. **Custom reports:** Spoken requests create, change and cancel recurring
   reports. Confirm trigger, facts and duration. Support end-of-lap nearby-car
   comparisons and gap-to-leader plus its change that lap. Report once per event
   using event-relevant data. Default duration: current session until cancelled.
3. **Multi-fact questions:** Retrieve available player/opponent lap and sector
   times, best/previous laps and race gaps. Resolve multiple facts and drivers
   in one request; label results and return available parts with clear omissions.
4. **Bounded tracking:** Compare Leclerc's braking points and minimum speeds
   with the player's over a requested lap. Confirm targets, metrics, regions
   and lap boundaries, accounting for different finish-line crossing times.
   Align observations, disclose sampling gaps, speak results and expire the
   task automatically. Support changes/cancellation without duplicate tasks.
5. **Focus-corner coach:** Support "Help me improve my worst corner for three
   laps." Select a measured loss region against an eligible reference or ask
   for a target if evidence is insufficient. Give one concise coaching focus
   and report subsequent measured progress against a fixed reference.
6. **Catch-or-defend watch:** Support "Tell me if Norris starts closing."
   Use multi-lap gap trends rather than isolated noise. Handle pits, overtakes,
   lapped cars and missing samples without false claims. Explain when the
   requested driver can no longer be meaningfully monitored.
7. **Consistency coach:** Identify variation, not merely slowness, across valid
   comparable laps at sector or supported region level. Identify the sample
   set, explain insufficient evidence and track later attempts on request.
8. **Target pace:** Support "Help me stay within three tenths of my best valid
   lap." Confirm target/reference; report sector/lap progress and observed
   deficits. Keep the reference fixed unless requested otherwise. Do not
   extrapolate missing sectors as observed results.
9. **Conversational context:** Include relevant previous requests, answers,
   instructions and tracking results in future request context. Resolve "What
   about his previous lap?", "Only sector two" and "Track that for another two
   laps"; clarify ambiguity. Preserve structured context if raw history is
   bounded. Old requests cannot reactivate cancelled/expired tasks. Reset
   session-specific context on session changes; cross-session history is optional.

## Post-pit same-compound benchmark

- After a confirmed pit stop and observed tyre change, speak one benchmark from
  opponents' eligible laps on the new compound during this same session.
- Snapshot reliable player/opponent positions before pit entry. Rank drivers
  with usable evidence by absolute position difference from the player's
  **pre-pit position**, not post-pit position. Default tie-break: closest reliable
  pre-pit race gap, then the driver ahead if still tied.
- Average an identified eligible stint/lap set: completed valid representative
  laps, excluding in/out laps and known interrupted laps. Disclose sample count,
  tyre-age range and material condition differences where available. A lone
  eligible lap is a single-lap reference, not a trend.
- Example: "You're on softs. Leclerc, one place ahead before your stop, averaged
  1:43.50 across four clean laps on softs this session." Example numbers cannot
  be spoken without evidence; the benchmark is not a guaranteed target.
- If the nearest driver lacks evidence, try the next nearest eligible driver.
  If none qualify, say so. Never silently substitute another compound/session.
  Disclose missing position data rather than claiming closest-driver selection.

## Standalone operation

- A configurable button on the actual MOZA R3 starts listening with the game
  focused. Distinct start/end sounds and a minimal ongoing cue communicate state.
  The assignment can be learned, saved and changed later without editing code;
  changes apply to a running radio and cannot accidentally activate capture.
  Support ending/cancelling capture; held/bouncing input cannot duplicate it.
  Cues and engineer playback cannot become unintended commands.
- With every browser closed, startup, telemetry, wheel input, microphone capture,
  questions, answers, proactive speech and tracking work. Essential settings,
  status and recovery controls remain accessible without a browser; visual
  review may remain browser-based. Dashboard closure/crashes cannot stop speech.

## Reliability and completion

- **Honesty:** No invented telemetry or stale-as-current values. Distinguish
  lap-time difference, race gap and gap change. Explain missing opponent data.
- **Radio discipline:** No overlapping speech, duplicate reports or stale
  backlog. "Quiet", "cancel that" and "repeat that" work with clear targets.
  Explicit questions take priority over routine coaching.
- **Task visibility:** "What are you tracking?" lists tasks, targets and expiry;
  "cancel all tracking" clears tasks and obsolete pending reports.
- **Session correctness:** Flashbacks, restarts, session/driver changes, pits,
  telemetry loss and reconnects cannot mix unrelated laps or create false claims.
- **Latency reporting:** Measure wheel-to-listening feedback, end-of-capture to
  first answer audio, and event-to-proactive audio on the actual setup. Report
  sample counts, representative timings, slow cases/timeouts and conditions;
  separate capture, transcription, reasoning, playback, queueing and deliberate
  delays where possible. **No fixed numerical latency gates.** The user decides
  whether to accept results or request substitutions, changes or optimisation.
- **Evidence:** Record automated/replay checks for partial data, multi-driver
  requests, task lifecycle and session resets. Demonstrate accepted abilities
  in real driving with the actual wheel, microphone and audible output, including
  browser-free operation. Record evidence and failed/pending checks per criterion.
  Replay alone is insufficient. User acceptance of functionality and reported
  latency is required before declaring the branch finished.

## Exclusions

Experiment mode and resource watch are discarded. Generic stint/end-of-session
debriefs are not required; the selected debrief is the post-pit benchmark above.
