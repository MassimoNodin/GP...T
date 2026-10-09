# Session engineer: agreed product decisions

Date: 2026-10-08

Status: accepted product direction, not implemented capability. Architecture
details and replacement of existing restrictive policies still need design.

## One session, two uses

The product supports radio-based live engineering and retrospective analysis.
While running, the app automatically detects telemetry sessions, processes and
saves evidence, and makes completed laps analysable during driving. Afterwards,
the same session is available without a stop-and-import prerequisite.
Temporary interruptions mark gaps rather than necessarily creating sessions.
Joining mid-session works with earlier unavailable evidence clearly identified.

Rationale: the architecture audit found separate live acquisition and archived
analysis paths. Shared processing is needed; removing UI controls is insufficient.
Raw capture, replay and reprocessing may remain internal diagnostic mechanisms.

## Primary app structure

Use two primary areas: Live Engineer and Session Review. Live Engineer presents
detected session/format, connection and radio status, push-to-talk setup and a
minimal list of active requests, without full analysis breakdowns. Session Review
provides saved sessions, lap/driver comparisons, detailed measurements, engineer
history and continued retrospective conversations.

Settings is secondary. Recording, import and diagnostic replay are outside the
everyday workflow. Session-specific engineer behaviour is selected automatically,
not through separate screens or a manually selected engineer mode.

Rationale: organise the app around the two intended uses rather than the
technical steps that acquire and process evidence.

## Session-aware engineer behaviour

Support distinct engineer behaviour for practice, every supported qualifying
format (including knockout, short, one-shot and sprint qualifying variants),
race variants and Time Trial. The one-career assumption simplifies archive
association; it does not exclude Time Trial or make every session behave as a race.

The engineer must use the detected format and available session progress to
interpret questions and choose relevant updates. Race context must include the
reported race length and progress, rather than assuming a fixed full-distance
race. Qualifying context must distinguish the format and stage. Preserve useful
session duration and remaining-time observations as well as total laps.

In races, automatic competitive updates follow the drivers immediately above
and below the player in race classification, not necessarily the closest cars
physically on track. Follow classification changes and treat on-track traffic
separately. Explicit requests remain pinned to their chosen driver. Interpret
competitive relevance against the reported race length and progress.

In qualifying, automatic competitive updates follow the drivers immediately
above and below the player on the timing sheet, not physical track neighbours.
These targets follow timing-sheet position changes; explicit requests remain
pinned to the chosen driver. Treat on-track traffic separately.

Automatic qualifying comparisons use best valid lap times from the current
stage for the player and selected timing-sheet neighbours, not recent average
pace. Competitive standings and explicit tracking references are separate:
explicit tasks keep their fixed latest usable completed reference by default,
unless the user requests another lap, such as the chosen driver's fastest lap.

Qualifying also provides selective format-specific timing and cutoff updates.
Multi-attempt formats use the detected stage and time remaining, including
whether another attempt is realistically possible when supported by evidence.
Elimination stages monitor meaningful changes relative to the advancement
cutoff alongside timing-sheet neighbours. One-shot formats prioritise the single
attempt and its resulting comparison, without suggesting another run. Sprint
qualifying/shootout variants use their own detected stage and timing context.
Avoid constant standings/countdown announcements and do not guess unknown rules
or timing.

Practice defaults to the player's own improvement: compare representative laps
for pace, consistency and corner-level differences, and give selective updates
when useful patterns emerge rather than after every lap. Opponent comparisons
and tracking remain available on explicit request; timing-sheet neighbours and
nearby cars are not automatically treated as competitive threats. Distinguish
measured patterns from unsupported claims about their causes.

Time Trial initially compares against the player's best valid, comparable saved
Time Trial lap with sufficient supporting telemetry at the same circuit/layout.
Keep that reference fixed throughout the attempt. When the player beats it,
report the improvement and also compare the new lap against the rival/ghost
selected in-game. Use the new PB for subsequent attempts and retain both
comparisons for retrospective analysis. Allow explicit selection of another lap.
Focus on meaningful gains, losses and recurring differences, not race threats.

Detailed rival comparisons require actual rival evidence. If only the rival's
lap time is available, limit the answer to supported timing comparisons. Do not
invent braking, speed or corner traces. Rival selection association and telemetry
availability still require implementation verification.

Further thresholds and implementation details remain discussion decisions.
Do not silently apply physical ahead/behind monitoring to every session format or invent missing
session metadata. Collection, explicit tracking, evidence safeguards and
session-isolated conversation still share the same processing foundation.

Rationale: relevance and useful timing depend on session objectives, while the
underlying evidence should not be split into separate capture/import products.

## Collection and automatic updates

- Retain available data for all drivers.
- Initially, automatic competitive lap updates concern the car ahead and behind.
- Automatic monitoring follows positions; history stays with each driver.
- Stable session-scoped identity is required; names are separate design work and
  car slots must not be assumed to have permanent occupants.
- Race updates use recent representative pace, initially the latest usable
  completed lap, developing a trend as evidence becomes sufficient rather than
  using personal bests. Other formats use the session-specific rules above.
- Qualify unsuitable laps and unknown conditions; never invent missing evidence.
- Speak selectively for meaningful findings or changes, not every completed lap
  or repeated unchanged findings. Wait for useful comparative evidence.

Rationale: notification relevance must not limit collection or explicit analysis.

## Explicit tracking

- Follow the chosen driver regardless of position changes.
- Immediately acknowledge understood requests with spoken Copy.
- Include evidence already collected in the requested lap and continue tracking.
- Start against the chosen driver's latest usable completed reference lap and
  keep that exact reference fixed throughout the tracked lap.
- Answer when useful evidence is ready, not merely because a lap has ended.
- This-lap requests end with the current lap; keep-tracking requests continue
  until stopped or the session ends. Historical questions do not start tasks.
- Ambiguous lap-scoped tracking defaults to the current lap and states its scope.
- Single-lap tasks finish with useful partial results or a brief limitation,
  never silently extending. Ongoing tasks continue without repeating limitations.

Rationale: ongoing engineering needs persistent task state and stable references,
not isolated questions about selected archived evidence alone.

## Tracking task controls

Support natural radio commands to stop a particular task, stop all explicit
tracking, change a task's chosen driver, and list active tasks. Stopping explicit
tasks does not disable automatic session updates. Changing the driver acknowledges
the change and establishes a new reference rather than silently reusing the old
driver's lap.

Distinct tasks can run concurrently; repeating the same request must not create
duplicates. Ask a brief clarification if a follow-up could affect several active
tasks. Cancelling stops monitoring and future task reports but retains collected
evidence and conversation history.

Rationale: radio control should not require dashboard interaction or risk
modifying the wrong task during driving.

## Comparison evidence and conditions

Allow supported measurements when both laps have sufficient corresponding
evidence, even if fuel, tyres or car conditions differ or are unknown. Qualify
these differences rather than requiring perfectly matched conditions for every
measurement. Measured differences do not by themselves establish their cause or
whether a driver's action was better. Explanations and judgements require
additional supporting evidence.

Missing channels, mismatched corners and unreliable coverage prevent affected
measurements. For automatic pace trends, omit unrepresentative laps such as pit
laps or known interrupted running where evidence identifies them; do not guess
the cause of a slow lap.

Rationale: preserve useful measured comparisons without overclaiming causality
or relaxing the integrity of the source evidence.

## Braking-point definition

Use the first meaningful brake application for a corner or braking zone, located
by track distance. Match corresponding zones using the same rule for both
drivers. Subsequent adjustments and trail braking are distinct measurements.
Qualify uncertainty due to sampling resolution and missing coverage. Later
braking is not automatically better; benefit needs supporting outcome evidence.
Exact detection thresholds and zone identification remain design work.

## Radio and interaction

- Deliver concise live summaries by radio, without full on-screen breakdowns.
- Save detailed measurements and references for retrospective review.
- Allow live spoken follow-ups about specific details.
- Delay automatic updates and tracking summaries during busy driving until a
  lower-workload opportunity; reassess stale queued competitive messages.
- Provide a settings toggle for the delay, enabled by default. When off, deliver
  results as soon as useful. Direct questions receive prompt answers and Copy
  acknowledgements remain immediate.
- Use configurable push-to-talk, submitting on release without transcript review;
  target keyboard and wheel/controller bindings.
- Ask short spoken clarifications when critical details are genuinely unclear.
- Retain typed retrospective interaction. Always-listening and wake-word
  activation are not required by the agreed initial scope.

## Review and conversational context

Provide lap analysis and engineer history. Preserve requests, responses,
automatic updates, exact target/reference identities, supporting measurements
and coverage qualifications. Do not save raw microphone audio by default.
Use relevant history and underlying evidence as context for subsequent questions,
so follow-ups retain established drivers, laps and corners. Previous spoken
answers are not substitutes for measurement evidence.

Every new session starts a fresh conversation context and no tracking tasks carry
over. Preserve old history for review; reopening a session provides its own
conversation context, isolated from other sessions.

Rationale: continuity enables follow-ups; isolation prevents unrelated history
and tasks leaking into new sessions.

## Initial career scope and cross-session comparisons

The initial product is designed for one career. At the user's direction, all
saved sessions are assumed to belong to that career; cross-session comparisons
do not require automatic save verification, save-profile confirmation or a
same-save gate. This supersedes the earlier same-save-only requirement.

Allow all saved sessions as candidate sources, while retaining relevant track,
layout, evidence and comparison compatibility checks. Do not interpret this
permission as making every lap a meaningful reference for every other lap.
Different-save contamination and changes in car development are accepted initial
risks, not verified equivalence. Do not describe the car performance or save
identity as verified. Multiple-career management is outside the initial scope.

Each session still has a fresh conversation context. Explicit cross-session
comparisons bring in selected reference evidence, not another session's entire
conversation history. Automatic live competitive updates concern current-session
drivers under the agreed session-specific reference policy, not arbitrary
historical archive laps.

## Accepted delivery order

The user approved foundation-first delivery: automatically detected sessions,
all-driver retained evidence, completed-lap analysis while driving continues,
and the same evidence after the session. Build contextual tasks/conversations
and radio on that foundation, then the two-area interface. Do not substitute
another visual redesign for resolving the live processing limitation.

The proposed technical migration is in
[the implementation plan](session-engineer-implementation-plan.md).

## Outstanding decisions

- Detection and useful-data thresholds for the agreed session-format behaviours.
- Exact session boundaries and reconnect handling.
- Driver association, names and reliable standings-target resolution.
- Incremental persistence, active evidence contracts and performance budgets.
- Corner identification, thresholds and useful-evidence criteria.
- Radio scheduling and device support; exact task-control intent handling.
- Existing capture migration and restrictive policy revisions.

No current implementation policy is silently relaxed by this document. Live,
opponent and race analysis require explicit replacement designs that preserve
evidence-integrity safeguards.
