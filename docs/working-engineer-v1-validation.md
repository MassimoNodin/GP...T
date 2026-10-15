# Working engineer v1 validation and live acceptance

Reviewed October 14, 2026 on feat/working-engineer-v1.
The [acceptance contract](working-engineer-v1-acceptance.md) is authoritative.
This is not branch completion or user acceptance. Synthetic tests and application
playback acknowledgements do not prove audible in-game operation.

## Automated verification

At code commit f14311f, the final complete Python run passed **3,596 tests**
with **16 platform-specific skips** and **two existing deprecation warnings**
in 319.34 seconds. Command: uv run --extra app --extra dev --extra radio pytest
-q --tb=short -rs. The skipped paths require Linux process isolation/procfs,
POSIX descriptor/FIFO operations, or portable symlink creation not available
in this Windows run; they are not recorded as passes. Warnings concern the
existing Starlette/httpx integration and raw-body upload API usage.

The final radio/name/wheel/conversation selection passed 1,539 tests; the
clarification module passed 43 tests, retaining its seven earlier regressions.
Radio modules compile and whitespace checks pass. No frontend build or Linux
runtime test was run in this increment. These results cover the bounded code
paths, not actual hardware or user acceptance.

## Live test preparation, October 14, 2026

Code commit 868a4d0 was verified with 3,612 passing Windows repository tests,
sixteen platform-specific skips and two existing warnings in 319.15 seconds.
On the actual Ubuntu host, 276 targeted authentication, request trace, live fact,
opponent capture, focus/watch/pit, radio diagnostic and local speech tests pass
with one existing warning. Compilation and PowerShell launcher parsing pass.
No UI/browser build, deployment or test was performed for this preparation.

Backend journal capture started before synchronization and service restarts.
The single-branch Ubuntu clone required an explicit feature-branch fetch and
tracking ref; the partial checkout was verified against the feature tree before
repairing its metadata, without discarding files or rewriting history. Ubuntu
now runs the feature code with the existing CPU affinity, demand-v1 opponent
policy, UDP address/port, Codex executable/profile and subscription model intact.

The configured career database lacked its speech pin. The existing verified
whisper.cpp 1.9.3 tiny.en CPU pin was reused, without replacing binaries/models
or bypassing hash checks. The old career acquisition journal requires a lengthy
recovery replay, so its retained data was kept and this live test uses a separate
NVMe database at:

    /mnt/nvme/f1-engineer/data/working-engineer-20261014-200122/f1-engineer.sqlite3

The run-specific service override is zzzz-working-engineer-test.conf in the user
f1-engineer.service.d directory. To restore the earlier database after the test,
stop the service, move only that named override into this run's log directory,
reload the user units and restart; expect the earlier recovery replay to resume.
Do not delete either database, mark recovery complete artificially, or seed test
facts into the real telemetry scope. Existing recordings remain in their original
NVMe location. Request tracing is enabled by the separate named trace override.

The isolated UDP receiver is bound on 192.168.1.115:20777 with a 212,992-byte
receive buffer. The actual runtime reports no error, zero packets/drops/socket
errors and awaits game input. Speech/runtime endpoints report ready. A bundled
JFK fixture was correctly transcribed through the real API in 1.500 seconds;
this is a fixture smoke check, not microphone or end-to-end voice latency.
Windows Hazel playback dispatched and native completion was acknowledged;
audibility/acoustic onset still requires the user's confirmation.

The normal Windows radio is running with automatic spoken submission, saved
R3 button 32, native playback and a dedicated SSH tunnel on port 19765. It does
not depend on the existing dashboard/tunnel on port 18765. A requested console
status was processed successfully. Its HTTP responses are 200, client/backend
request IDs match, and the corresponding records are present in the Ubuntu
journal capture. No process/thread failure was logged during readiness checks.
The R3 is currently not detected and F1 is not providing telemetry; stale facts
are unavailable rather than spoken as current. Capture remains idle until input.

Local ignored evidence directory:

    data/radio-tests/working-engineer-20261014-200122/

It contains preflight/device/binding/voice manifests, deployment commands,
Windows playback diagnostics and radio/radio.jsonl with stderr/launcher logs.
The local run was checked for the actual credential value: no leak was found.
Radio logs rotate at 25 MiB with four backups and include spoken text, but not
raw microphone audio or HTTP credentials/bodies. Do not publish unreviewed logs.

Backend evidence directory:

    /mnt/nvme/f1-engineer/logs/working-engineer-20261014-200122/

It contains before/after service configuration and revision, deployment logs,
readiness, fixture transcription and the continuing backend-journal.jsonl.
The private journal collector unit working-engineer-20261014-200122-logs runs
for up to four hours; normal systemd journal logging remains enabled afterwards.
The telemetry database supplies retained data-level evidence separately.

Actual wheel/microphone driving, listening cues, audible answers/coaching,
browser-free acceptance and acoustic latency sign-off remain pending. Connect
the wheel, select the intended Windows default playback/microphone devices,
enable F1 UDP toward the listening Ubuntu endpoint and begin a real session.
No successful real-game criterion or user acceptance is claimed by this setup.

## Criterion status

Every criterion still needs a real-game demonstration and user acceptance.
The [mapping](working-engineer-v1-mapping.md) and
[standalone guide](working-engineer-standalone-radio.md) describe implementation
boundaries and automated validation in detail.

| Criterion | Implemented path | Live check / limitation |
| --- | --- | --- |
| Proactive comparisons/coaching | Event-bound lap/gap reports, opponent diagnostics, fixed-reference focus practice. | Audible comparisons and assessment of experimental repeatability suggestions. Opponent differences are not causal prescriptions. |
| Custom reports | Spoken create/update/cancel/list/expiry, nearby comparisons, leader gap/change. | Confirm trigger/facts/duration and one call per event, including changes/cancellation. |
| Multi-fact retrieval | Qualified current/last/observed/game-reported timing and multi-driver partial results. | Compare answers to game facts; hear omissions. Unobserved older laps cannot be recovered. |
| Bounded tracking | Fixed identities, independent next laps, owned captures and aligned windows. | Real admission/completion, sample disclosures, expiry and demand release. |
| Focus-corner coach | Largest admitted retained-window loss or clarification; one fixed-reference focus. | Real comparable reference and audible progress. No exhaustive circuit ranking or physically verified braking prescription. |
| Catch/defend | Fixed named target, three-boundary trends, qualified direct/derived gaps. | Real closing/defending, pits, overtakes, lapping and informative source-change pauses. |
| Consistency | Three fully observed consecutive valid same-compound laps; observed sector spreads. | Warm-up, variation call and invalid/missing-sample reset. Not a pace-deficit claim. |
| Target pace | Identified observed valid reference, fixed tolerance, sector/lap progress. | Real reference and subsequent calls. Not unqualified complete-session best or inferred missing sectors. |
| Conversation | Bounded structured history, follow-ups, task versions and clarification IDs. | Spoken follow-ups, ambiguity and cancelled/expired reference refusal. Not unrestricted language. |
| Post-pit benchmark | Confirmed service/compound change, pre-pit ranking, eligible stint averages/fallback. | Actual stop, correct nearest eligible driver/lap set and audible disclosures. |
| Wheel/cues | Saved live-reloadable mapping, bounce guards, echo-safe start/pulse/end capture. | R3 press with F1 focused, reassignment, audible cues and no duplicate capture. |
| Browser-free app | Standalone transport, wheel/microphone/native playback and console controls. | Startup, questions/tasks/proactive calls and recovery with every browser closed. |
| Reliability | Freshness, bounded queues/history, priority, lifecycle/version guards and partial-data refusal. | Real reconnect, restart/flashback, quiet/repeat/cancel and telemetry loss. |
| Latency | Capture/transcription/planning/queue/dispatch diagnostics. | Actual acoustic onset, sample counts, slow cases/timeouts and explicit user decision; no numerical gate. |

Missing fuel, traffic, physical-layout verification and continuous context are
disclosed, not assumed. Named regions require a matching reviewed catalog. Only
a draft model was found in bundled data; synthetic reviewed-model tests are not
real-circuit geometry evidence. Numeric windows remain usable without inventing
corners. Unobserved historical data and inferred fastest sectors are unavailable.

## Read-only local readiness inspection

Inspection on October 14, 2026 found:

- R3 mapping preserved: GUID 030007036e3400000500000000000000, button 32.
- Controller discovery returned no connected devices in this environment.
- No matching F1 process was found. Neither port 8765 nor usual tunnel port
  18765 was listening.
- The default radio status check reported radio_token_file_unavailable.
  No credential was printed or manufactured.
- Audio discovery worked; defaults were an Oculus virtual microphone and LG
  ULTRAWIDE output. Headset routing and audibility were not verified. Re-enumerate
  before selecting indices, which may change between runs.
- Voice discovery returned Microsoft Hazel Desktop and Microsoft Zira Desktop.
  Inspection did not trigger playback or microphone capture.

No new real-game, wheel-press, microphone, acoustic-latency or browser-free
acceptance evidence was obtained. An approved backend/tunnel with transcription,
live telemetry, connected wheel and user participation are needed.

## Live acceptance procedure

Use the standalone guide and existing approved backend/speech deployment. Local
startup is documented in README; Ubuntu uses the established authenticated tunnel
and token file. Do not start a competing UDP receiver or replace production
runtimes/models merely to complete this checklist.

1. Connect R3/headset, re-enumerate controllers/audio and inspect the saved mapping.
   Retain button 32 unless the user chooses another; learn replacements through
   the documented radio-input bind command.
2. Start the approved backend/tunnel and F1 telemetry. Require actually available
   data from radio --once with correct API/token settings. Close every browser
   and run normal interactive radio, not dry-run/capture-test or manual-submit.
   Optionally retain JSON output locally with PowerShell Tee-Object; never commit
   credentials, personal logs or raw microphone data.
3. With F1 focused, press the wheel button. Hear start, ongoing and end cues.
   Ask "my and leader last lap and position" and check available facts against
   the game. Record omissions and capture/playback failures, not just successes.
4. Request "tell me at the end of lap a comparison between my car and cars around
   me" and "tell me at the end of lap my time to leader and delta that lap".
   Drive the necessary boundaries. Different completed laps must make aligned
   comparison unavailable, not become a race-gap estimate.
5. Request the agreed Leclerc braking/minimum-speed task. Reply with a real
   numeric window or reviewed label before either original next lap begins.
   Verify independent lap labels, one report, disclosures, expiry and released
   demands. Failed admission must not shift the task onto later laps.
6. Collect an eligible complete player region, then request three-lap focus
   practice. Try worst-corner wording with/without sufficient retained evidence.
   Check one focus, fixed reference, bounded progress and clarification.
7. Test named catch/defend over three qualified boundaries, consistency over
   three fully observed valid comparable laps, and "help me stay within three
   tenths of my best valid lap" after an eligible reference exists. Missing
   evidence must reset, pause or refuse instead of inventing results.
8. Observe opponents' eligible same-compound laps before a serviced tyre-change
   stop. Verify benchmark pre-pit ranking, driver, actual lap set/average and
   omissions. Also test no eligible evidence and no confirmed service/change.
9. Exercise follow-ups, listing, clear-reference cancellation, explicit task IDs,
   cancel-all, quiet/resume and repeat. Check priority, no overlapping speech
   and no resurrected tasks after expiry/cancellation/resets.
10. Reassign the wheel button while radio runs; release/press the new button.
    Exercise wheel reconnect, telemetry loss and game restart or flashback.
    Record actual recovery/failures without manipulating telemetry to fake passes.

## Live microphone check: missing-session feedback

On October 14, 2026, the Razer Barracuda X 2.4 microphone was explicitly
selected as input device 1 (MME); mono 16 kHz input settings passed validation.
The user heard wheel-button cues but reported no spoken response to two hello
attempts. Both captures transcribed as "Hello?"; capture-end-to-transcript
times were 1.992 and 2.031 seconds. No F1 telemetry was available. The voice
submission guard discarded drafts without a session, leaving only a console
diagnostic and no audible explanation.

Completed, valid captures started without a session now queue a spoken
no-live-telemetry explanation instead of silently discarding the request.
This does not execute the transcript, create tasks, or relax the three-second
freshness limit. Cancelled captures, invalid wheel origins, and captures from
a changed or stale previous session remain rejected. A new session cancels
queued no-session feedback. Regression tests cover these boundaries and
one-time consumption. Audible user confirmation remains pending.

## Live question failures and concise radio replies

On October 15, 2026, live traces showed five microphone transcripts: three
"Okay." clips, "This one, Mark.", and "What was my last laptop?". Four were
processed with live telemetry available; their unsupported-question fallback
spoke a long feature catalogue instead of a useful brief acknowledgement.
Captured audio durations were 0.78 to 1.82 seconds. The traces cannot establish
the actual words spoken or whether the capture omitted the start of a question.

Unrecognized questions now receive only "Didn't catch that. Say again."
No-session feedback is also short. Timing answers omit internal car-slot
identifiers from speech while preserving them in structured diagnostics.
Natural present-position and leader-gap phrasing is accepted, as is "what was
my last lap time"; historical-position requests are still rejected rather than
answered with current data. Ambiguous transcription such as "laptop" is not
silently converted into a telemetry request.

Capture-completion traces now include peak level, RMS dBFS and clipped-sample
fraction without retaining raw microphone audio. These support distinguishing
quiet/clipped input from recognition failures on the next real microphone test.
Recognition accuracy and acoustic response latency remain unverified; the
pinned speech model and three-second telemetry freshness guards are unchanged.

## Wheel lag and access-conflict incident

On October 15, 2026, the user reported severe computer lag while the wheel was
powered on and loss of F1 wheel control. A local input check had reported
IDirectInputDevice8::Acquire() HRESULT 0x80070005. The engineer radio was stopped
and left off. The user restored game control by restarting the wheel in F1 and
confirmed that the lag stopped when the wheel was powered off. This association
does not independently establish the driver-level cause.

Inspection found joystick construction/initialization on every poll, plus
one-second reacquisition attempts after driver failure. Handles are now cached
until removal, with hotplug additions opened once. Recognized access denial
releases the source and latches wheel input off for that process without further
automatic acquisition, including after button reassignment. Sanitized failure
traces preserve exception frame/type chains. Ordinary reconnect behavior remains
covered by mocks. No physical wheel access or engineer restart was performed to
validate this patch; shared game access, absence of lag and wheel-triggered
speech must be reverified with the user before accepting the branch.

## Latency and final sign-off

Measure wheel press to audible listening feedback, capture end to first answer
audio, and actual event to first proactive audio on the same real run. Report
sample counts, representative timings, slow cases/timeouts, device/backend
conditions and deliberate capture/queue delays. There is no fixed latency gate.

Application diagnostics separate request-to-listening, capture duration/gaps,
end-to-transcript, planning and queue wait where available. Dispatch is only a
playback request, not acoustic onset. Never subtract independent cross-host
monotonic clocks or present synthetic snapshot timings as voice latency.
User-observed or synchronized recorded acoustic evidence is still required.

Record pass/fail/pending per criterion, scenario, actual observations, diagnostic
evidence and limitations. Present latency for the user's decision. Only explicit
acceptance of functionality and latency closes the branch.
