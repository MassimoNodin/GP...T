# Standalone lap radio foundation

This Windows client provides browser-independent playback of fresh player lap
completion events and a deterministic last-lap answer. It is a partial increment
towards [working engineer v1](working-engineer-v1-acceptance.md), not the finished
engineer or completion of the standalone-radio acceptance slice.

## Start and inspect

Run from the repository root in an interactive Windows console. The backend must
already be running with the [live facts endpoint](working-engineer-live-facts.md).
No browser, Next.js server, microphone or model is needed by this client.

```powershell
uv run --extra app f1-engineer radio --list-voices
uv run --extra app f1-engineer radio --once
uv run --extra app f1-engineer radio --dry-run
uv run --extra app f1-engineer radio
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
| `quiet` | Cancel speech and queued calls; suppress proactive calls, not explicit answers. |
| `resume` | Re-enable future proactive calls; do not replay suppressed calls. |
| `cancel` | Cancel current speech and queued messages. |
| `repeat` | Repeat the active or last completed message within its current session/player binding. |
| `status` | Print backend availability and radio state as JSON. |
| `quit` | Stop playback and close the client/helper; Ctrl+C also stops the client. |

Closing stdin stops the radio. Commands are fixed console controls, not natural
language questions. No microphone is opened and no listening cue is emitted.

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

## Validation and remaining work

Automated tests cover scheduling, interruption, quiet/repeat/cancellation,
identity/epoch changes, freshness across hosts, malformed/missing snapshots,
history gaps, bounded authenticated transport, CLI no-audio modes and a mocked
native helper. Installed voice discovery was also exercised on Windows.
The focused radio/CLI/live-facts run passed 100 tests; the full backend regression
passed 1,820 with 16 skipped and two existing deprecation warnings. Compilation,
CLI help and installed voice discovery also passed. Actual audible playback,
live-game behaviour and wheel input are not verified by those checks.

Still required: MOZA R3 activation with the game focused, microphone capture and
listening cues, transcription/conversation integration, multi-driver queries,
task tracking and the selected coaching/benchmark abilities. Real-game,
browser-closed acceptance remains pending. See the
[capability mapping](working-engineer-v1-mapping.md) for subsequent slices.
