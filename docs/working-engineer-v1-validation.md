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
