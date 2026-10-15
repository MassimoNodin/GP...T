# Non-conflicting wheel input investigation

Investigated October 15, 2026. The engineer remains stopped. This investigation
does not open, acquire, register for, or send commands to a physical controller.
Importing pygame to inspect version metadata does not initialize its joystick
subsystem. No Windows/game/controller configuration was changed.

## Evidence and likely conflict mechanism

The installed pygame-ce is 2.5.8 with SDL 2.32.10. In the matching upstream SDL
release, the Windows DirectInput joystick backend calls SetCooperativeLevel
with DISCL_EXCLUSIVE and DISCL_BACKGROUND (source lines 767-771). This happens
when opening the joystick, not only when the application requests an actual
force-feedback effect. The application currently opens that backend through
pygame.joystick.Joystick. It cannot make that path nonexclusive merely by
avoiding force-feedback calls in Python.

The controlled live test failed in joystick opening with an Acquire access
denial while F1 was running. The upstream exclusive-access request provides
a concrete mechanism for competing device ownership. It is consistent with
the observed game-control conflict; it is not proof of the precise driver-level
cause of all reported computer lag. Caching handles and stopping retries after
access denial address churn and failure safety, not this exclusive-access mode.

## Implementation update

The native nonexclusive/background reader is now the Windows default for wheel
monitoring and input discovery/learning. A bounded 60-second test with F1 running
detected the saved R3 button 32, acquired once and closed cleanly; the user
confirmed the game worked fine. The full engineer remained stopped. See
working-engineer-v1-validation.md for measurements and remaining voice/gameplay
acceptance work. The investigation below records the alternatives considered.

## Option 1: native nonexclusive DirectInput

Recommended first bounded prototype: bypass pygame/SDL and use a Windows
helper that explicitly requests DISCL_NONEXCLUSIVE and DISCL_BACKGROUND.
Microsoft documents nonexclusive access as not interfering with other clients,
including when another client has obtained exclusive access. This still needs
verification on the actual MOZA driver and with F1 focused.

- Read buttons only; never request exclusive cooperation or create/play forces.
- Open the selected controller once, retain its handle, and close on shutdown.
- Use buffered button transitions plus current state so short presses are not
  lost between polls. Keep release-before-arming, debounce and reconnect guards.
- Keep a fixed-duration watchdog and stop on driver failures; do not fall back
  to SDL exclusive acquisition or repeatedly reacquire after denial.
- Adapt to the existing WheelDevice/InputFrame/monitor interfaces. Dynamic
  button assignment and standalone/browser-free operation must remain intact.
- Verify physical button mapping; do not assume a saved pygame index is identical
  to a native DirectInput object number without checking. Preserve the existing
  saved assignment until an explicit migration/rebinding step.

This is a smaller conceptual change than interpreting arbitrary HID reports,
but requires native Windows interface and lifetime/error-handling work. A shared
access request is a design constraint, not a guarantee that this driver accepts it.

## Option 2: native Windows Raw Input

Register a hidden window for joystick/gamepad HID input with RIDEV_INPUTSINK,
then receive WM_INPUT messages while F1 has focus. Use RIDEV_DEVNOTIFY for device
arrival/removal. Microsoft documents background delivery and per-device HID
data access. This route avoids calling DirectInput Acquire entirely.

- Decode button usages using HID preparsed data and HidP functions, not guessed
  byte offsets or assumed MOZA report layouts. Handle multiple report IDs.
- Select the correct device/top-level HID collection and retain stable identity;
  reject ambiguous matches and rearm after reconnect or binding changes.
- Do not request input suppression, capture keyboard/mouse input, send output
  reports, or modify game/device settings. A hidden message window is an OS
  input mechanism, not a dashboard/UI dependency.
- Bound queued reports and copied HID data. Release registrations and buffers
  on shutdown. Verify that reports actually arrive with F1 focused.

This is a strong fallback if native shared DirectInput is refused. It is more
implementation work because HID usages, report layouts and device identity must
be translated. It is not equivalent to enabling SDL's Raw Input hint: the
matching SDL backend targets XInput-compatible gamepads rather than generic
wheel/joystick HID collections. Do not treat that hint as a verified MOZA fix.

## Option 3: game-generated UDP button events

The repository already recognizes BUTN event packets and preserves their raw
details, but does not decode button state into a live radio trigger. An
alternative could consume game-generated button events without opening the
wheel in the engineer process. Before adopting it, verify the F1 25 event
payload, which in-game actions are transmitted, and whether a suitable action
can be assigned to the requested physical button without disturbing driving.

This route is conditional, not verified. It depends on the game emitting
telemetry, so it cannot replace independent listening when the game is closed
or telemetry is silent. It would also need event de-duplication, rising-edge
detection, session/reset guards, bounded retention and measured network latency.
The raw game button mask must not be assumed to use pygame button numbering.

## Not preferred

Do not patch/rebuild the bundled SDL binary, change wheel drivers/firmware,
install virtual-wheel or interception drivers, run as administrator, or disable
F1 force feedback as the first solution. These add risk or change the driving
experience without demonstrating that they fix shared input. Vendor-supported
keyboard remapping remains a possible fallback only if support for this exact
wheel and button is independently verified; no such support was established here.

## Next verification gate

Implement and mock-test one explicitly nonexclusive input provider, including
all error cleanup, identity, short-press and reconnect cases, before reopening
hardware. With the user's agreement, run a short wheel-only test with F1 focused.
Record whether button 32 is detected, input access mode, open count, poll/CPU
timings and cleanup. User confirmation of normal F1 control and no lag is
mandatory. Only then test microphone/speech through the full engineer. Do not
automatically restart the stopped radio during this investigation.

## Primary sources checked

These sources were fetched directly with successful HTTP responses. Web-search
tool calls did not return usable results. No third-party claims are used to
establish the Windows access semantics or SDL implementation.

- SDL 2.32.10 Windows DirectInput implementation:
  `https://raw.githubusercontent.com/libsdl-org/SDL/release-2.32.10/src/joystick/windows/SDL_dinputjoystick.c`
- Microsoft IDirectInputDevice8::SetCooperativeLevel:
  `https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee417921(v=vs.85)`
- Microsoft About Raw Input:
  `https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input`
- Microsoft Using Raw Input:
  `https://learn.microsoft.com/en-us/windows/win32/inputdev/using-raw-input`
- Microsoft RegisterRawInputDevices:
  `https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerrawinputdevices`
- Microsoft HidP_GetUsages for HID button decoding:
  `https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidpi/nf-hidpi-hidp_getusages`
- SDL 2.32.10 Raw Input backend scope:
  `https://raw.githubusercontent.com/libsdl-org/SDL/release-2.32.10/src/joystick/windows/SDL_rawinputjoystick.c`
