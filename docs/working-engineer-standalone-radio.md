# Standalone radio foundation

This Windows client provides browser-independent playback of fresh player lap
completion events, bounded live timing questions and explicit transcript-draft
submission. It is a partial increment
towards [working engineer v1](working-engineer-v1-acceptance.md), not the finished
engineer or completion of the standalone-radio acceptance slice.

## Start and inspect

Run from the repository root in an interactive Windows console. The backend must
already be running with the [live facts endpoint](working-engineer-live-facts.md).
No browser or Next.js server is needed. Lap playback needs no microphone or
model; microphone capture and pinned backend transcription are optional.

```powershell
uv run --extra app f1-engineer radio --list-voices
uv run --extra app f1-engineer radio --once
uv run --extra app f1-engineer radio --dry-run
uv run --extra app --extra radio f1-engineer radio
```

`--list-voices` discovers installed Windows System.Speech voices without backend
access or audio playback. `--once` fetches and validates one snapshot without
audio; it exits with 0 for available data and 2 for unavailable data. `--dry-run`
runs the event/command loop and emits JSON diagnostics without playing audio.
Normal mode speaks through a hidden persistent PowerShell/System.Speech helper.

The default API address is `http://127.0.0.1:8765` and the default token file is
`data/.f1-engineer-control-token`. For an already-established split-host tunnel:

```powershell
uv run --extra app f1-engineer radio --api-url http://127.0.0.1:18765 --token-file data/.f1-engineer-ubuntu-control-token
```

The existing companion launcher can establish transport but also starts the
dashboard; the radio itself has no dashboard dependency. This command does not
create the tunnel, start or upgrade the backend, or deploy production services.
Use the approved deployment workflow if the backend lacks the live endpoint.
Never place tokens in command arguments or share token files in diagnostics.

Optional settings: `--voice "Microsoft Hazel Desktop"`, `--rate 0`,
`--volume 100`, and `--poll-interval 0.25`. Select a voice reported by
`--list-voices`; rate is -10 to 10, volume 0 to 100, and polling 0.1 to 1 second.
The helper does not change Windows default voice settings.

## Console controls

Enter one command per line; an optional leading `/` is accepted.

| Command | Behaviour |
| --- | --- |
| `lap` | Speak the current player's last completed lap, or explain that timing is unavailable. |
| `listen` | Start capture, or end the current capture when issued again. |
| `finish` | End the current capture and request transcription (except in capture-test mode). |
| `ask <question>` | Answer a supported live timing question; cancel any capture or older draft. |
| `submit` | Submit the current session-bound draft once, as a supported question or lap-report instruction. |
| `tasks` | Speak the active lap-report tasks, targets, facts and expiry. |
| `forget` | Clear conversation references and cancel capture, drafts and queued/active speech. |
| `quiet` | Cancel capture, drafts, speech and queued calls; suppress proactive calls, not explicit answers. |
| `resume` | Re-enable future proactive calls; do not replay suppressed calls. |
| `cancel` | Discard capture/transcription/draft and cancel speech and queued messages. |
| `repeat` | Repeat the active or last completed message within its current session/player binding. |
| `status` | Print backend availability, radio state, wheel binding and voice-input state as JSON. |
| `quit` | Stop playback and close the client/helper; Ctrl+C also stops the client. |

Closing stdin stops the radio. Use `ask` for supported natural-language questions;
other commands remain fixed console controls. `lap` and `repeat` cancel voice capture before their answer
plays. Dry-run, once, device-listing and wheel-only testing never record audio.

## Learn or change the wheel button

Wheel support is optional and installed with the `radio` extra. No button number
is hardcoded. List the currently connected controllers, select the MOZA device's
index, then press the desired button when prompted:

```powershell
uv run --extra app --extra radio f1-engineer radio-input list
uv run --extra app --extra radio f1-engineer radio-input bind --device-index 0
uv run --extra app --extra radio f1-engineer radio-input show
uv run --extra app --extra radio f1-engineer radio-input test
uv run --extra app --extra radio f1-engineer radio
```

Replace `0` with the index from `list`. Button learning waits up to 30 seconds;
`--timeout` accepts 1 to 120 seconds. Release held buttons before pressing the
chosen button. An aborted/timed-out learning attempt leaves the old assignment
intact. `test` emits requests without backend access, audio or microphone capture;
use Ctrl+C to stop. Test again with the game focused on the actual MOZA R3.

The saved assignment lives in `data/radio-input.json` (ignored by Git). Re-run
`bind` at any time in another console to change it; a running radio reloads the
settings every half second without a restart. Use `radio-input clear` to disable
wheel activation. For a custom file, place `--input-settings PATH` before the
`radio-input` action, and pass the same option to `radio`. Neither `show` nor
`clear` opens a controller. No SDL subsystem is opened by an unbound radio.

Assignments match controller GUID and name, not enumeration index. SDL GUIDs
identify device types rather than guaranteeing unique hardware serials: two
identical matching devices cause an explicit ambiguous-device status, never a
guess. Disconnects, changed button counts and invalid settings fail closed.
Held buttons on startup/rebinding/reconnection cannot activate until released;
press/release edges are tracked with a short debounce, including taps between
polls. Background input is enabled without creating a visible window.

The bound button now toggles microphone capture in normal radio or capture-test
mode. Wheel-only `radio-input test` remains diagnostic: it does not record audio.

## Microphone capture, cues and transcript drafts

Install the `radio` extra when running capture. List audio devices without
opening a microphone or playing a sound, then run the backend-free local test:

```powershell
uv run --extra app --extra radio f1-engineer radio --list-audio-devices
uv run --extra app --extra radio f1-engineer radio --capture-test
uv run --extra app --extra radio f1-engineer radio --microphone 1 --cue-output 3
```

Indices in the last example are illustrative; use the list from your machine.
Audio device indices are a current-run selection, not a persistent hardware
identity, and can change when devices reconnect. Defaults follow the OS input
and output devices without changing OS settings. `--cue-volume` sets tonal
amplitude (greater than 0, at most 0.3; default 0.08). Cue output selection affects
tones only; Windows engineer speech still uses the OS default playback device.

Nothing records merely because the radio starts. Press the bound wheel button
or enter `listen` to start; press again, enter `finish`, or reach the configured
limit to finish. `--capture-seconds` accepts 1 to 12 seconds, default 12, matching
the existing pinned transcription backend's maximum clip size. `cancel`, `quiet`
and shutdown discard the clip. Session changes, wheel disconnects, rebindings
and reconnect-instance changes invalidate wheel-owned capture and pending drafts.
Console capture does not depend on a wheel.

A rising two-tone start cue precedes microphone opening; a short, lower end cue
follows microphone closure. A quieter pulse every two seconds indicates ongoing
listening. The microphone is aborted/paused during pulses and a 0.2-second guard
after them; those intervals are omitted from the clip. This reduces cue leakage
but can omit speech spoken during a pulse/guard. `capture_gap_s` reports an
aggregate gap estimate. Actual acoustic isolation is not verified; evaluate this
trade-off with the headset in the full test before accepting the implementation.
No engineer speech plays during starting/capture/stopping. Routine reports are
dropped, not queued for a later stale backlog.

Capture uses mono 16 kHz signed 16-bit PCM and a canonical bounded WAV entirely
in memory. Capture-test mode reports duration and sample counts, but neither
uploads nor saves a recording and does not require a token/backend. Normal mode
posts the clip to the existing authenticated `/api/v1/engineer/transcribe` route
with a UUID and SHA-256 digest. Replies must match both correlations and the
bounded transcript contract. Existing runtime/model pins are unchanged. The
backend must already have its approved speech runtime; this command installs or
deploys no model/service. Backend temporary transcription files follow the
existing cleanup policy; the radio itself persists no audio or transcripts.

The console prints `radio_transcript` with `verified: false` and
`submitted: false`. It is a **draft**, not an automatically submitted question
or an executable control command. Text may still be retained by your terminal
or logging setup. Review the text and use `submit` to send it as a timing question,
or `ask <corrected question>` to replace it. Explicit submission accepts supported
timing questions and lap-report task instructions, never arbitrary console controls.
It does not automatically submit a transcript. This prevents mistranscriptions or tones
from automatically becoming actions.

Native audio work and transcription run off the radio loop. Cancellation aborts
the asynchronous HTTP request and suppresses late results. New capture cannot
overlap a still-cleaning-up worker. Operational safeguards cancel stalled audio
startup after five seconds, overdue capture after its limit plus one second,
and stalled finishing after two seconds; pending cleanup is reported, not
misrepresented as success. These are resource safety timeouts, not latency
acceptance gates. Device/overflow/cue errors discard the clip rather than
presenting damaged audio as a successful request.
If microphone closure fails, new capture and engineer playback remain blocked
and the radio reports that a restart is required, rather than assuming safety.

## Reliability and timing

The radio seeds its event cursor on startup and reconnect: historical laps are
not announced. Only newly observed, fresh player lap completions are proactive.
Missing or stale facts, invalid bindings, session/epoch/player changes and event
history gaps invalidate queued speech rather than guessing facts or catching up
with obsolete calls. Questions interrupt lower-priority routine calls. The
scheduler bounds its queue and deduplication memory, expires output and prevents
overlapping speech. Repeat is an explicit replay, not a newly observed fact.

HTTP access is authenticated and restricted to loopback, with redirects and
environment proxies disabled. Token reads and response bodies are bounded;
diagnostics do not expose tokens or backend response bodies. User speech text
travels as JSON stdin data, never executable PowerShell source.

JSON events include `queue_wait_s`, estimated `observation_to_dispatch_s` and
`completion_observed_s`. Cross-host event age uses server-relative receipt
differences plus snapshot age and conservative client transport time; it never
subtracts Windows and Ubuntu monotonic clocks. Dispatch means an application
playback request, and completion means a native completion acknowledgement
observed by the loop. Neither proves audible output or measures acoustic onset.
Dry-run completion is simulated. These diagnostics are measurements, not latency
acceptance gates; real-game latency still needs a report for user assessment.
Capture adds application-observed `request_to_listening_s`, `capture_elapsed_s`,
`capture_audio_s`, `capture_gap_s` and `end_to_transcript_s`. The last includes the
end cue and backend transport/transcription, not reasoning or answer playback.
They do not measure acoustic cue onset or prove that output was audible.

## Validation and remaining work

Automated tests cover scheduling, interruption, quiet/repeat/cancellation,
identity/epoch changes, freshness across hosts, malformed/missing snapshots,
history gaps, bounded authenticated transport, CLI no-audio modes and a mocked
native helper. Installed voice discovery was also exercised on Windows.
The initial focused radio/CLI/live-facts run passed 100 tests; its backend regression
passed 1,820 with 16 skipped and two existing deprecation warnings. Compilation,
CLI help and installed voice discovery also passed. Subsequent button-binding
tests cover learning, saved assignments, live changes, held/bouncing input,
disconnects, ambiguity and safe degradation. The input update passed 1,863 full
regression tests with 16 skipped and the same two warnings; the final focused
input/radio/CLI run passed 114, including six additional routing/timeout cases
added after the full run had collected its tests. Native controller enumeration was
exercised on Windows and found no connected controllers in this environment.
Actual audible playback, live-game behaviour and physical wheel input are not
verified by those checks.

The wheel was subsequently connected, enumerated as R3 Racing Wheel and Pedals,
and button 32 was learned successfully. Game-focused activation was deferred by
the user. Native audio enumeration also succeeded without recording or playing
sound. Actual capture, cue audibility, transcription and listening latency have
not been demonstrated on the user's headset.
The listening increment passed 221 focused capture/input/radio/CLI/speech/facts
checks and 1,926 full regression tests with 16 skipped and the two existing
deprecation warnings. Its 57 new tests use fake audio/HTTP adapters, not recorded
microphone evidence. Compilation, CLI help and documentation-link checks passed.

The live-question increment passed 266 focused radio/input/listening/facts/CLI
checks and 1,986 full regression tests with 16 skipped and the same two existing
warnings. Its 60 new cases include a mocked capture-to-transcript-to-explicit-
submission-to-playback path, not real microphone or in-game evidence. Compilation,
CLI help, local documentation links and staged whitespace checks also passed.

The verified-name increment passed 358 focused roster/facts/detail-policy/radio/
input/listening/CLI checks and 2,022 full regression tests with 16 skipped and
the same two existing warnings. Its 36 new cases include decoded F1 25 and 2026
roster packets, identity-bound publication and named radio answers, conflicting
rosters, replacement, duplicate surnames, Unicode/quoted names and parser safety.
Compilation, CLI help and local documentation links also passed. These remain
synthetic packet/audio tests, not live-game or hardware acceptance evidence.

Still required: MOZA R3 activation with the game focused, capture/cue hardware
acceptance, broader conversational planning and historical timing retrieval,
braking/minimum-speed tracking and the selected coaching/benchmark abilities. Real-game,
browser-closed acceptance remains pending. See the
[capability mapping](working-engineer-v1-mapping.md) for subsequent slices.

## Live timing questions and follow-ups

This first conversation increment uses a deterministic typed plan, not a new model
or a change to the recorded-evidence router. Supported facts are last completed
lap time, current lap elapsed time, current race position, gap to the leader and
gap to the car immediately ahead. Current lap elapsed is not a projected or
completed lap time. Last lap time does not establish that the lap was valid, a
personal best, or on comparable tyres. Gaps are the driver's own UDP race-gap
fields, never subtraction of lap times or an inferred gap to any arbitrary car.
Missing, zero-sentinel, malformed or ambiguous observations yield partial
unavailable results, not invented numbers.

Request up to three targets and three facts, for example:

```text
ask What are my and the leader's last lap times and positions?
ask And current lap times?
ask What about the car behind me?
ask My gap to the leader and gap to the car ahead
ask P3 last lap and position
ask My and Leclerc and Norris last lap times and positions
ask "Charles Leclerc" last lap
```

Targets are me/my, leader, car ahead/in front, car behind, P1 through P24, and
available participant full names or surnames.
P numbers are race positions, not car slots or race numbers. Replies additionally
identify the verified roster name when available and the zero-based car slot.
Older numbered laps, bests, sectors, tyre benchmarks and strategies
are explicitly unsupported in this increment. Recurring lap reports use the
separate controls below. A
mixed supported/unsupported request is rejected rather than silently dropping
the unsupported part. This is not yet the complete multi-driver acceptance slice.

Names come only from current, identity-bound participant metadata. Exact full
names and last-word surname aliases are case-insensitive and Unicode-normalized;
accent stripping, fuzzy matching, first-name guesses and official-ID lookups are
not used. Duplicate surnames or duplicate full names yield an explicit ambiguous
result and ask for a full name or race position, never the first match. Reserved
names such as `me` or `leader`, and numeric-only names, require double quotes.
Numeric suffixes such as the `16` in `Player 16` are not surname aliases. Unknown
or unusable names cannot be bound; use an available name or a current P position.
Legacy servers without name metadata retain player/position-based queries.

The last eight accepted requests and their typed plans/driver bindings are held
only in memory. Follow-ups inherit omitted facts or targets from the most recent
accepted request. An omitted target follows the previously selected participant
identity and tenure, even if positions change; an explicit new `leader` or `P3`
resolves the current position again. A disappeared/replaced driver is unavailable,
not rebound to the new occupant. Missing targets stay unresolved in follow-ups
until explicitly requested again. Previous numerical values are never used as
new facts. Unavailable/stale telemetry, session/player/epoch changes, restart and
`forget` clear context. Invalid requests do not overwrite the prior accepted plan.
This does not yet infer references to older turns or persist coaching preferences.

`submit` consumes one draft only in its still-current session/player scope. It
can create/update/list/cancel the supported lap-report tasks only after explicit
submission; it cannot execute `quiet`, `quit` or arbitrary console controls through
transcript text. Wheel assignment/connection changes retain the capture cancellation rules.
Both explicit submission and typed questions cancel competing queued speech.
`ask`/`submit` are disabled in capture-test mode. No browser is needed.

`radio_question_answer` reports the original question, per-target results and
`planning_s`; existing speech events report queue wait and observation-to-dispatch
age. These are local processing/dispatch observations, not acoustic or end-to-end
hardware latency. Fresh timing answers must start before their observations reach
the existing three-second freshness limit; that deadline does not cut off an
already-started multi-fact answer while live telemetry remains healthy. A separate
60-second utterance expiry bounds stuck playback. Session invalidation still
cancels speech, and stale timing answers cannot be replayed with `repeat`. These
are safety limits, not a user latency acceptance gate.

## Configurable end-of-lap reports

This coarse-timing task increment adds a bounded, in-memory registry to the
standalone radio. It uses the existing coherent lap events and all-driver timing
facts; it acquires no extra driving traces and changes no detail-mode gates.
Create reports through `ask` or review a transcript and explicitly `submit` it:

```text
ask Tell me at the end of each lap my and Leclerc last lap times and positions
ask Tell me at the end of each lap my gap to the leader for the next 3 laps
ask Tell me at the end of next lap my last lap time
ask What are you tracking?
ask Update task 1 tell me at the end of each lap my gap to the car ahead
ask Cancel task 1
ask Cancel all tracking
```

`each`, `every`, `the`, or no qualifier before `lap` creates a recurring report
until cancellation or scope loss. `next lap` means the next observed completion
of the currently in-progress player lap, not a full trace of the following lap.
`for the next N laps` accepts one to twenty upcoming completions. The spoken
acknowledgement gives the first and final lap numbers; updating resets that
schedule and increments the task version. These timings do not define the later
next-lap braking/minimum-speed tracking contract.

Facts and targets use the live-question vocabulary. Named drivers are resolved
unambiguously at creation/update and stay bound to that participant identity and
tenure. Missing/replaced named targets are unavailable, not rebound by name.
`leader`, `car ahead`, `car behind` and P positions are explicit dynamic roles,
resolved again at each player-lap completion. `self` follows the bound player.
Task instructions require an explicit supported timing fact and do not inherit
omitted targets from conversation history. Task changes do not overwrite the
previous accepted question's follow-up context.

The registry allows three active tasks and nine total driver/fact pairs per lap.
Identical active requests with the same reference bindings and expiry are
idempotent. Failed creation/update leaves existing tasks intact. `tasks` and
`what are you tracking` speak active IDs, targets, facts and expiry; `status`
also shows versions, reference policy and completed finite tasks. Completed
records are pruned on subsequent successful creation/update, and IDs are not reused.
Nothing is persisted across a radio restart.

Reports trigger only from a new, verified player lap-completion event matching
the current player lap; an available event time must agree with the current last
lap time. Each driver's completed lap number is spoken separately, since drivers
may be on different laps. Missing own/opponent timing produces partial unavailable
results. Last-lap comparisons use the guarded computed fact described below.
Player lap-boundary race-gap changes are described below; best-lap coaching
and predictions are not implemented.

All due tasks form one bounded proactive utterance. It replaces the default
player-only lap call for that event and uses the shared speech scheduler. If the
combined text exceeds the safe utterance bound, the radio asks for fewer facts
instead of crashing or silently truncating them. Routine reports never overlap
questions. Quiet/listening suppresses calls without retaining a backlog; finite
tasks still consume those lap completions. Expiry without a usable final event
invalidates obsolete owned output.

Any update/cancellation invalidates queued, active and repeatable task-report
bundles. Owned cancellation itself preserves unrelated answers; explicit new
console/submitted requests still use the normal cancel-competing-speech policy.
Other active tasks remain registered
and report on the next eligible event. Final finite reports may finish speaking
after their task becomes completed. Session/player/epoch/generation changes,
unavailable/stale telemetry, unknown/invalid player lap numbers, rewinds/skipped player laps, player discontinuities
and event-history gaps clear tasks and obsolete output. Reconnects require
explicitly creating new reports; no historical events are replayed.

`radio_task_created`, `radio_task_updated`, `radio_task_ended`, `radio_task_report`
and `radio_task_answer` expose lifecycle, plans and output alongside existing
dispatch latency events. No real-game latency gate, model/runtime substitution
or automatic transcript execution is introduced. Coaching and detailed tracking
remain separate unfinished slices.

Validation for this increment: 408 focused tests and 2,072 full-suite tests passed,
with 16 skipped tests and two existing deprecation warnings in the full suite.
The 50 new task cases cover scheduling, reference binding, lifecycle, partial
facts and invalidation. Radio compilation, CLI help and local documentation-link
checks also passed. This is synthetic/mocked evidence, not verification of
in-game speech, wheel input, microphone capture or acoustic latency.

## Computed last-lap comparisons

Questions and configured lap reports can now compare an opponent reported
last-lap time with your reported time using integer milliseconds. Examples:

```text
ask Compare my last lap with Leclerc and Norris
ask Leclerc last lap comparison and position
ask Tell me at the end of each lap compare my last lap with the leader
ask Tell me at the end of each lap Leclerc last lap comparison for the next 3 laps
```

The comparison is always relative to you: an opponent taking less time is
described as faster than you, and equal reported times are described as matched.
Both drivers must have a known, identical completed lap number and positive
bounded integer times. Otherwise the comparison is explicitly unavailable;
different lap numbers are spoken so the missing comparison is understandable.
An opponent who has not yet completed your latest lap is not compared to a
different lap or to cached historical values. Other requested facts still work
when the comparison is unavailable.

The same name/role binding, follow-up, freshness and report lifecycle rules
apply. No extra telemetry or model calls are required. These are reported-time
comparisons, not verified valid-lap records, like-for-like tyre/weather/stint
benchmarks or estimates of race-gap changes. Lap validity and comparable driving
conditions are not established by this coarse timing fact. This does not add
best-lap coaching or opponent lap history.

Validation: 359 focused tests passed, including 42 new comparison cases. The
full suite passed 2,114 tests with 16 skipped tests and two existing deprecation
warnings. Radio compilation, CLI help and local documentation-link checks passed.
These checks use synthetic/mocked inputs; live-game speech and acoustic latency
have not been measured for this increment.

## Lap-boundary race-gap changes

The standalone radio observes your reported gap to the leader and the car ahead
at qualified player lap completions, independently of active report tasks:

```text
ask Tell me at the end of each lap my time to the leader and delta that lap
ask Tell me at the end of each lap my gap change to the car ahead
ask My gap change to the leader
```

Here `delta that lap` explicitly means your gap-to-leader change. A reduced gap
is spoken as gained time, an increased gap as lost time, and equal gaps as
unchanged. Output identifies the completed lap and says the change was measured
between observed player-lap boundaries. This is not a subtraction of two drivers
lap times, a prediction, or proof that driving pace caused the change. The sample
is taken from the snapshot carrying the qualified event, not an exact line-crossing
measurement. Observation age is exposed with `radio_gap_change_observed`; existing
dispatch latency diagnostics and freshness limits still apply.

Two consecutive comparable completions are required. The first completion seeds
a reference and reports unavailable rather than labelling a mid-lap sample as a
whole-lap change. References must have the same participant identity and race
position throughout observed snapshots. Player and reference must have known
on-track pit status and racing result status, valid positive gaps, and the same
current lap number at both completion samples. A reference may cross the line
before you between samples without breaking the interval. Lapped references,
leader/ahead changes, position changes, pits, missing data, unknown status,
rewinds, discontinuities, skipped/missing completions, event-history gaps and
three-second observation interruptions invalidate the affected interval. Any
reported driver discontinuity conservatively resets both intervals. Session,
player, generation and epoch changes clear them entirely.

Only your own gap changes are supported in this increment; requests for an
opponent gap change return a partial unavailable result while other facts remain
usable. Results expire when you move to another lap or the relevant reference
becomes ineligible. Obsolete owned answers and bundled lap reports are cancelled
and cannot be repeated. Missing results are not filled from question history.
Quiet/listening suppresses speech without stopping observations or building a
backlog. No data is persisted, no additional detail demands are acquired and no
model/runtime settings are changed. `Cancel all tracking` also resets these
in-memory boundary references. This is a foundation for, not completion of,
multi-lap catch/defend coaching.

Validation: 445 focused tests passed, including 51 new gap-change cases. The
full suite passed 2,165 tests with 16 skipped tests and two existing deprecation
warnings. Radio compilation, CLI help, staged whitespace and local documentation
link checks passed. This is synthetic/mocked verification; in-game speech,
wheel-only operation and real acoustic latency remain unverified.

## Fixed-driver catch/defend watches

The standalone radio can register a bounded adjacent-driver watch through a
typed request or an explicitly submitted transcript:

```text
ask Tell me if Norris starts closing
ask Watch the car ahead
ask Watch Leclerc for the next 5 laps
ask Update task 1 watch Norris
ask What are you tracking?
ask Cancel task 1
```

A watch shares the existing three-task/nine-fact budget, IDs, update, cancellation,
expiry and scope-reset controls. It requires one explicitly resolved driver and
cannot be combined with additional facts in that task. Names, initial race
positions and initial ahead/behind roles all freeze the selected participant
identity. The acknowledgement confirms the driver; an overtake does not silently
retarget the watch. A non-adjacent named driver can be registered, but the watch
pauses until the same driver is eligible and adjacent. `status` exposes the watch
version, comparable-boundary count, role, notice state and current eligibility
reason. Listing tasks identifies watches as fixed-identity references.

Ahead watches use your gap to the car ahead. Behind watches use the watched
driver's gap to their car ahead, only after verifying that you occupy that unique
adjacent race position. No race gaps are invented by subtracting unrelated
drivers' leader gaps. Both cars need known on-track/racing status, unique valid
race positions, valid positive gap values and compatible lap progression. Ahead
references must align with your new lap at the completion sample. A same-race-lap
driver behind may still be on your just-completed lap until they cross the line;
that expected phase difference is accepted, but additional lap separation is not.

Three consecutive qualified player-lap boundaries provide two observed intervals.
Both intervals must shrink by at least 0.100 seconds and the total reduction must
be at least 0.300 seconds to announce closing. The mirrored rule announces
opening. Otherwise the watch reports no consistent closing/opening trend, not
that the driver has stopped closing. These are explicit noise-filter policies,
not latency acceptance gates. They do not establish equal tyres, valid lap times,
driving pace, future catching time or a recommended driving manoeuvre.

The watch speaks once when collecting samples, when a supported trend changes,
or when its eligibility changes to a paused condition. It does not repeat the
same trend every lap. Ineligible/missing snapshots, pits, role/position changes,
replacement, lap misalignment, discontinuities and observation interruptions
discard the trend window; recovery needs fresh samples. Session/history scope
failures follow the existing task invalidation rules. Obsolete bundled watch
speech is cancelled and cannot be repeated when the reference becomes invalid.

Watch notices and ordinary lap reports combine into one owned proactive utterance.
A watch-only event with no new notice is silent rather than falling back to the
default player lap call. Quiet/listening still consumes observations and notices
without replaying a backlog. Finite watches consume each completion even during
warm-up or ineligibility; very short watches may expire without enough evidence.
`radio_watch_notice` logs task/version, lap and notice text, alongside existing
dispatch timing events. No detailed telemetry demands, persistence, model/runtime
changes or automatic transcript execution are added. Non-adjacent catch/defend
analysis and real-game/audio acceptance remain outside this increment.

Validation: 496 focused tests passed, including 51 new watch cases. The full
suite passed 2,216 tests with 16 skipped tests and two existing deprecation
warnings. Radio compilation, CLI help, local documentation links and staged
whitespace checks passed. Evidence is synthetic/mocked; game-focused wheel
activation, real audio, acoustic latency and user acceptance remain unverified.

## Fixed-reference lap target pace

The standalone radio now supports lap-end target-pace tasks:

```text
ask Help me stay within three tenths of my best valid lap
ask Help me stay within 0.3 seconds of my best valid lap for the next 4 laps
ask Update task 1 help me stay within two tenths of my best valid lap
ask What are you tracking?
ask Cancel task 1
```

The target is the fastest eligible lap fully observed by this radio connection
on your current actual tyre compound, not an unobserved whole-session or career
best. The radio must observe qualified lap boundaries spanning the lap, with
known game-valid, on-track, racing and unchanged tyre-compound data throughout
its observed snapshots. A first partially observed lap is never a reference.
The completion must explicitly report the previous lap as game-valid, have a
positive bounded time and match the current reported last-lap time. Pits,
invalid/unknown flags, missing tyre data, compound changes and observation gaps
make that lap ineligible, even if a later snapshot looks healthy again.

If no eligible reference exists on the current compound, the request is refused
without replacing existing tasks. Complete a valid lap with the radio connected
first. The acknowledgement and task list identify reference lap number, reported
time, compound code and tolerance. Reference and tolerance are immutable for
that task version; a subsequent faster lap updates the observed-best inventory
but never moves the active target. Explicit updates choose a fresh eligible best.
The registry also rejects malformed or missing fixed references.

Tolerances accept one to nine tenths in words or digits, or seconds with up to
three decimal places, bounded to 0.001–5.000 seconds. Requests may run indefinitely
or for one to twenty upcoming completions. The target is a maximum allowed
slowdown: a faster lap meets it, a slower lap at exactly the tolerance is within
it, and an overrun reports both the reference delta and excess beyond tolerance.
All calculations use integer milliseconds.

The same observed-lap evidence is required for feedback. Missing evidence is
spoken as unavailable, not a pace deficit. A fully observed lap on another
compound pauses comparison against the fixed reference; an explicit update is
needed to select another compound reference. This does not establish matching
weather, fuel load, tyre age or stint conditions and makes no prediction about
future laps. Actual compound codes are reported without inventing tyre names.

Tasks share the existing capacity, expiry, cancellation, owned speech, freshness
and quiet/listening rules and combine with other lap reports into one utterance.
`status` includes observed bests; `radio_pace_lap_observed` and
`radio_target_pace_report` expose eligibility, frozen reference and output alongside
dispatch timing. At most sixteen compound bests are retained in memory. Session,
player, epoch/generation, unavailable telemetry, history-gap and player-lap
discontinuity resets discard the inventory. `Cancel all tracking` also clears it.
No new detail demand, persistence, model/runtime change or automatic transcript
execution is added. Observed sector-progress feedback is described below.
Whole-session best retrieval and live-game/audio acceptance remain unfinished.
Observed consistency coaching is described below.

Validation: 558 focused tests passed, including 62 new target-pace cases. The
full suite passed 2,278 tests with 16 skipped tests and two existing deprecation
warnings. Radio compilation, CLI help, local documentation links and staged
whitespace checks passed. This is synthetic/mocked evidence, not verification
of in-game speech, acoustic latency or the full target-pace acceptance criterion.

## Observed sector target-pace feedback

The same target-pace requests now include progress after sectors 1 and 2 when
the frozen reference has complete observed sector timings. Both timings must
come from that single fully observed valid reference lap; the radio never mixes
best sectors from different laps or infers sector 3. The acknowledgement and
task status identify whether sector feedback is available. A reference without
sector metadata still provides lap-end feedback and explicitly reports sector
comparison as unavailable.

At each boundary, the radio compares cumulative elapsed time with the same
boundary of the fixed reference. The whole-lap tolerance is the allowance
so far, not a per-sector allocation or a predicted lap result. For example,
after sector 2 it can report that you are cumulatively 0.200 seconds behind
the reference and within the 0.300 second lap allowance so far.

Current timings require fresh player-bound sector events aligned with the
current lap, sector index and reported duration. Sector 2 also requires an
observed sector 1 whose reported value has not changed. Invalid/unknown flags,
pits, missing evidence, observation interruptions and changed compounds never
produce a numeric deficit. A known different compound pauses the comparison.
Missing or inconsistent sector metadata does not invalidate otherwise eligible
lap-end feedback. An equally fast newly observed whole lap with complete sector
metadata can replace an inventory best that lacked metadata, but active tasks
retain their original reference until explicitly updated.

Sector speech shares existing quiet/listening and freshness safeguards. New
sector progress replaces older owned sector speech, and lap completion, task
controls, eligibility loss and observation/scope interruptions invalidate it.
Duplicate observations are silent; quiet/listening still collects evidence
without building a speech backlog. Sector progress never consumes a finite
lap schedule. Multiple eligible target tasks combine into one bounded utterance.
`radio_target_pace_sector` records task/version, reference, lap, sector and text
alongside existing dispatch timing evidence. No new detail demand, model/runtime
change or automatic transcript execution is added.

Validation: 607 focused tests passed, including 49 new sector-pace cases. The
full suite passed 2,327 tests with 16 skipped tests and two existing deprecation
warnings. Radio compilation, CLI help, local documentation links and staged
whitespace checks passed. Evidence remains synthetic/mocked; in-game speech,
wheel/audio behaviour and acoustic latency still require the deferred full test.

## Observed sector consistency coaching

The standalone radio supports these bounded player-only tasks:

```text
ask Coach my consistency
ask Track my consistency for the next 6 laps
ask Update task 1 monitor my consistency for next 4 laps
ask What are you tracking?
ask Cancel task 1
```

Each lap-end report uses a rolling window of three consecutive fully observed
game-valid laps on one actual tyre compound, with sectors 1 and 2 observed and
aligned on each lap. Only laps completed after the task version was requested
are included; updates start a new window. The first partial observed lap never
qualifies. Missing sectors, invalid laps, pits, unknown flags, tyre changes and
sampling interruptions restart warm-up rather than borrowing unrelated laps.
Session/player/history discontinuities retain the existing task reset rules.

After three samples, the report names their lap numbers and compound, then gives
the fastest-to-slowest range for the whole lap and separately for sectors 1 and
2. The greater sector range provides one repeatability focus; equal ranges are
reported as equal, including zero variation. It does not identify a corner,
infer sector 3, claim a pace deficit or diagnose a driving mistake. These are
same-compound observed samples, not verified fuel/weather/tyre-age equivalence;
the spoken report explicitly states that limitation. Subsequent valid laps
refresh the rolling sample without claiming causal improvement.

Warm-up reports state the available sample count. Finite tasks expire on the
requested lap schedule even if insufficient evidence prevents a full report.
Quiet/listening continues to consume observations without a speech backlog.
Reports combine with other lap tasks into one bounded owned utterance; task
controls, lost eligibility and observation discontinuities cancel obsolete
owned speech. The tracker retains at most three lap records, independently of
the existing fixed target-pace best inventory. No new detailed telemetry demand,
model/runtime changes, persistence or automatic transcript execution is added.

`radio_consistency_report` logs task/version, lap, exact sample records and
report text alongside existing dispatch timing evidence. Corner-level
consistency analysis and live-game/audio acceptance remain outside this increment.

Validation: 641 focused tests passed, including 34 new consistency cases. The
full suite passed 2,361 tests with 16 skipped tests and two existing deprecation
warnings. Radio compilation, CLI help, local documentation links and staged
whitespace checks passed. Evidence is synthetic/mocked; in-game speech,
wheel/audio behaviour and measured acoustic latency remain unverified.

## Live player observations for corner-work foundations

The authenticated live feed now carries last-reported canonical session context
bound to the current session UID and packet format, and a player-only observation
with speed, brake, throttle, steering and gear, alongside lap number, elapsed
time and distance. The observation includes verified driver identity, epoch,
frame ordinal/identifier, session time and separate lap/telemetry receipt
provenance. This is measurement access, not corner authority or coaching.

The standalone radio can retrieve observed speed and brake input with:

```text
ask What is my speed and brake input?
ask My and the leader speed
```

Speed is reported in kilometres per hour and brake input as a percentage,
explicitly not a braking point. Multi-driver requests retain partial results;
opponent detail is unavailable through this feed. Detailed opponent acquisition
still requires the existing detail-demand policies and ownership. No opponent
demands are created and no Gate A selection rules are expanded by this increment.

Player telemetry must match the lap packet session, format, frame, player slot
and exact session clock, and have bounded known receipt provenance. Missing,
conflicting, clock-mismatched or invalid channel values produce unavailable
observations without breaking otherwise valid lap facts. No prior-frame
telemetry is filled forward. The radio additionally checks driver identity,
scope/frame provenance, lap values and telemetry age independently of lap age.
Queued answers and lap reports expire against the oldest included fact, so a
newer lap packet cannot extend the dispatch lifetime of older speed/brake data.
Existing three-second freshness safeguards remain safety checks, not latency
acceptance targets. Stale/failed runtime snapshots suppress both observations
and context, without mutating the cached source.

Session context does not identify a verified layout, an approved corner model
or stable conditions throughout a lap; unknown decoded fields remain unknown.
Both context and observations explicitly remain non-coaching evidence. Existing
time-trial corner ranking and diagnostic session-comparison gates are unchanged.
Focus-corner selection, fixed references, connected regional measurement,
braking/minimum-speed tracking and proactive progress feedback remain unfinished.
No runtime/model pins, persistence, deployment or automatic voice submission
behaviour change. Real-game availability and acoustic latency remain unverified.

Validation: 702 focused tests passed, including 50 new live-observation cases.
The full rerun passed 2,411 tests with 16 skipped tests and two existing
deprecation warnings. An initial full run hit a recording-inbox HTTP 503 in
the recording start/stop test; the 63-test recording/observation isolation run
and full rerun both passed without changes to recording code. Compilation,
radio CLI help, local documentation links and staged whitespace checks passed.
Evidence is synthetic/mocked, not live-game, wheel/audio or acoustic-latency
acceptance, and does not complete the focus-corner coaching requirement.

## Bounded numeric-region measurement prerequisite

`analysis.corners.analyze_distance_window` measures one explicitly selected numeric
distance window without inventing a named corner or using a draft model as
layout authority. It reuses the existing distance resampling, source-session
continuity, sampled minimum-speed and sustained brake-onset machinery. No
time-trial ranking or diagnostic comparison policy is broadened.

The result contains elapsed time only when both boundaries, the complete
interior time grid and the underlying source session chronology are supported.
Unknown channels remain unknown. Minimum speed is the minimum of observed raw
samples in the half-open window, with its source anchor and coverage, not an
estimated true continuous minimum. A disconnected source cannot be labelled a
complete-window minimum. Brake onset requires a sustained sampled threshold
episode and retains its distance bracket; braking already active at the window
entrance is censored rather than presented as a measured braking point.

The window is limited to 1,000 metres, 4,096 source samples and a fixed one-metre
grid, with the existing 0.1-second/25-metre interpolation limits. Bounds, finite
channel ranges and source frame chronology are validated before measurement.
Input samples are not modified. Missing boundaries, gaps and clock/distance
regressions cannot be bridged into a regional elapsed-time claim.

This is an analysis-library prerequisite, not a newly available radio command.
Its caller must supply a single identity/lap/session-bound trace and enforce
validity, pit status, receipt freshness and detail-demand ownership. This helper
does not infer those facts from raw samples or establish condition equivalence.
Every result explicitly remains diagnostic, layout-unverified, non-ranking and
non-coaching evidence. Live task-owned collection, fixed-reference comparisons,
spoken focus/progress reports and opponent next-lap tracking remain unfinished.
No wheel, speech runtime, deployment, persistence or model configuration changes.

Validation: 187 focused tests passed, including 50 new numeric-window cases.
The full suite passed 2,461 tests with 16 skipped tests and two existing
deprecation warnings. Analysis/test compilation, radio CLI help and whitespace
checks passed. All measurement evidence is synthetic; this does not verify
live collection, in-game speech, hardware behaviour or acoustic latency, and
does not complete focus-corner coaching acceptance.

## Next-lap numeric-region radio tracking

The standalone radio now accepts bounded player-only observation tasks:

```text
ask Track my braking and minimum speed from 100 to 250 metres for next lap
ask Track my braking and minimum speed from 100 to 250 metres for next 3 laps
ask Update task 1 track my braking and minimum speed from 150 to 300 metres for next lap
ask List tasks
ask Cancel task 1
```

The first tracked lap is the upcoming lap, not the partially observed current
lap. Each task owns an immutable numeric window and track length, its existing
task/version identity and a finite one-to-twenty-lap schedule. Standard task
limits, update/cancel, session/driver bindings and history-gap resets apply.
Unknown or scope-mismatched track context prevents installation. Updates discard
the old version's samples and start on the next lap; expiry releases buffers.

Collection retains only one pre-window sample and the selected window through
its first exit sample, capped at 4,096 samples per task/lap. No additional UDP
receiver, opponent detail demand, persistence or automatic transcript submission
is introduced. Fresh aligned speed/brake observations use the same source,
identity and independent telemetry-age checks as instantaneous radio retrieval.
Invalid/pit/non-racing observations, unknown or changed compound, conflicting
duplicates, rewinds, changed track length and missing aligned telemetry inside
the selected window poison the lap's result. Identical frame duplicates do not
create additional samples. Measurement never spans task versions or lap numbers.

A fresh matching lap-completion event produces one combined owned lap-report
utterance, with sampled minimum speed, sustained brake-onset bracket and measured
region elapsed time when supported. Partial source/grid coverage is disclosed;
missing or censored onset and unsupported elapsed time remain unavailable. The
sampled minimum is retrospective lap evidence, not current speed or a claim
about the true continuous minimum. Logs include task/version, lap/event, player
and session binding, compound, source frame bounds and diagnostic measurement.
Quiet/listening still consume finite tasks without accumulating speech backlog;
queued reports use the existing fresh lap-completion dispatch deadline.

The current HTTP polling default is 0.25 seconds. That is coarser than the
existing 0.1-second connected-measurement requirement and will commonly yield
partial minimum-speed coverage and unavailable onset/elapsed time. The existing
`--poll-interval 0.1` option may improve capture, but network/scheduling gaps can
still prevent complete support. No defaults or continuity guards were weakened.
Task-owned high-rate backend buffering remains a prerequisite for dependable
full-resolution live tracking; sparse polling must not be advertised as that.

These are explicit numeric diagnostic observations, not named-corner coaching,
fixed-reference progress comparisons or opponent braking/minimum-speed tracking.
Those features, wheel-only voice execution and live-game/audio acceptance remain
unfinished. Model/runtime pins, deployment and OS audio defaults are unchanged.

Validation: 750 focused tests passed, including 53 new regional-radio cases.
The final saved full run passed 2,514 tests with 16 skipped tests and two existing
deprecation warnings. An initial completed full run hit the previously observed
recording-inbox HTTP 503; the 66-test recording/regional isolation run passed
without recording-code changes. An intervening full run lost its final tool
output and is not counted as successful verification. Compilation, radio CLI
help and staged whitespace checks passed. Evidence remains synthetic/mocked;
live-game capture, wheel/audio output and acoustic latency are not verified.

## Task-owned backend regional capture

The live backend now provides authenticated regional capture leases through
`POST`, `GET` and `DELETE /api/v2/engineer/region-captures/{lease_id}`.
Admission binds a fresh lowercase hexadecimal lease ID to the live generation,
session, player identity, numeric window and upcoming one-to-twenty-lap schedule.
Each observed assembled frame reaches the existing regional analyzer without
requiring an intermediate HTTP poll or introducing another UDP receiver. Only
active requested windows retain detailed player samples; opponent detail-demand
permissions and full/demand-profile selection remain unchanged.

At most three captures are active, with sixteen retained lease records and a
thirty-second renewable TTL. Repeating the identical admission renews a lease
without discarding evidence; changing its immutable request requires a new ID.
Release, expiry, runtime/identity changes and publication failure clear owned
samples and results. Completed captures retain at most their requested lap
count, remain renewable while the same binding is fresh, and expire without
renewal. No historical frames are backfilled on admission or journal recovery.

The publication owner holds the capture guard across its SQLite transaction;
API admission and reads cannot expose uncommitted measurements. Rollback
invalidates captured evidence. Reads return detached diagnostic results and
backend-computed `completion_age_s`; future clients must add transport age rather
than compare monotonic clocks across processes. Existing telemetry freshness,
window bounds and connected-measurement continuity guards are unchanged.

This increment is backend/API infrastructure only. The standalone radio client
still uses the polling tracker described above and does not yet arm, renew,
read or release these leases. The next slice must integrate leases with task
versions, updates/cancellation, shutdown, fresh dispatch and duplicate-speech
prevention while keeping network requests off the input/audio control loop.
Numeric results remain layout-unverified diagnostic observations, not named
corner coaching, fixed-reference comparisons or opponent tracking. The branch
remains unfinished; no live-game, wheel/audio or acoustic-latency proof is added.

Validation: 904 distinct focused tests passed, including 49 new capture cases,
with one existing Starlette/httpx deprecation warning. The full suite was not
rerun for this backend-only increment. Compilation, radio CLI help and whitespace
checks passed. Evidence is synthetic/mocked; model/runtime pins, deployment and
OS audio defaults are unchanged.

## Standalone radio capture lease integration

The standalone radio now uses backend capture leases for the existing numeric
player-region tracking commands. This supersedes the polling-only collection
limitation and the client-integration prerequisite in the two preceding sections.
The polling interval still controls snapshot/report delivery, but no longer
controls how many assembled player frames the backend can collect for a region.
Full measurement still depends on actual connected, valid source data; missing
or partial coverage remains disclosed rather than interpolated beyond the
existing guards. No named-corner, fixed-reference or opponent feature is added.

Each task version receives a run-unique lease ID and immutable session/player,
upcoming-lap schedule and window binding. Task creation acknowledges admission
as pending, not already collecting. The existing backend worker admits and reads
leases alongside polling, renewing every ten seconds; the wheel/audio control loop only exchanges
detached desired requests and completed responses. Admission latency and consumed
result completion age/request latency are emitted as diagnostic events. There is
no new numeric latency acceptance gate or live acoustic-latency measurement.

Updates release the old version before admitting a new lease. Cancellation,
finite completion, stale runtime and binding changes withdraw desired captures;
shutdown releases owned leases before closing the worker's HTTP client. A
cancellation during an in-flight admission prevents publication of its result
and triggers release. Release is best-effort if the backend is unreachable;
the existing thirty-second backend TTL provides cleanup without successful
renewal, and failed release is logged rather than claimed as successful.

Temporary admission/read transport failures can retry the same immutable ID.
Rejected admission does not silently retarget a later lap or reactivate an ended
lease. A matching fresh lap event is required for speech: task/version, lease,
request, runtime/player binding, window, schedule and event identity must agree.
Backend-computed completion age includes request and local delivery age before
dispatch qualification, without comparing cross-process monotonic clocks.
Duplicate or stale results, malformed text and evidence claiming coaching or
ranking authority are rejected. Missing capture evidence produces an explicit
unavailable report; this mode never quietly substitutes sparse polling evidence.
Quiet/listening consume finite tasks without creating a speech backlog.

Validation: 740 focused radio/backend-capture tests passed, including 41 new
lease-integration cases, with one existing Starlette/httpx deprecation warning.
Compilation, radio CLI help and whitespace checks passed. The full suite was
not rerun for this increment. All evidence is synthetic/mocked, including the
complete regional report produced without intermediate radio polls inside the
window. Real-game frame capture, wheel-only voice execution, browser-closed
wheel/audio acceptance and live acoustic latency remain unverified. The branch
is not complete; deployment, model/runtime pins and OS audio defaults are unchanged.
