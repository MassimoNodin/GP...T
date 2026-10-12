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
| `submit` | Submit the current session-bound transcript draft once, as a question only. |
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
or `ask <corrected question>` to replace it. Submission never interprets draft text
as console controls or task instructions. This prevents mistranscriptions or tones
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

Still required: MOZA R3 activation with the game focused, capture/cue hardware
acceptance, verified named-driver queries, broader conversational planning,
task tracking and the selected coaching/benchmark abilities. Real-game,
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
```

Targets are me/my, leader, car ahead/in front, car behind, and P1 through P24.
P numbers are race positions, not car slots or race numbers. Replies additionally
identify the zero-based car slot until verified participant names are available.
Named drivers, older numbered laps, bests, sectors, tyre benchmarks, strategies
and recurring instructions are explicitly unsupported in this increment. A
mixed supported/unsupported request is rejected rather than silently dropping
the unsupported part. This is not yet the complete multi-driver acceptance slice.

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
cannot execute `quiet`, `quit`, task controls or instructions through transcript
text. Wheel assignment/connection changes retain the capture cancellation rules.
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
