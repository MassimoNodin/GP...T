# Remaining-page UI parity audit

Date: 2026-10-07. Audited commit: `7bc5461045555b61b5c9fe2eb504a8acd1d7ee21`, branch `codex/mock-ui-replication-20261006`.

## Result

**Dashboard, Sessions, AI Engineer, Recordings and Settings all fail full visual and functional parity.** Their broad panel arrangements resemble the references, but the main compositions still show unlabelled example data and disconnected controls. Real workflows exist below them in collapsed disclosures. Dashboard's live telemetry card is the exception: it now reads pinned operation evidence.

This audit records findings and screenshots. Application code was not changed.

## Method and limits

- Inspected all details in mock images 1, 3, 5, 6 and 8 and captured the corresponding application pages at **1672×941**, from the top, without selected session parameters. Image metadata confirms all eight mocks have that size. Used full viewport exports with explicit bounds where needed; each retained JPEG was visually inspected and its dimensions checked independently.
- Compared shell geometry, headings, panel boundaries, row sizing, tables, imagery, controls, status labels, units and source information.
- Measured DOM panel rectangles and input properties; [measurements](../artifacts/ui-parity-audit/layout-measurements.json) preserve the results. CSS subpixel measurements are rounded below. Mock panel coordinates are visually measured, so comparisons are approximate rather than pixel-diff scores.
- Clicked representative harmless controls and checked the resulting accessibility state. Each of the five tested main-page controls produced no change beyond focus.
- A `gpt-6.1-sol` specialist independently audited the main compositions and real route workflows, as required by `AGENTS.md`.
- Current recording and storage proxies returned HTTP 503. The runtime proxy returned HTTP 403 from the preview request; the real Settings workflow also showed runtime status unavailable. Live service-backed operations could not be accepted during this audit.
- The production build and TypeScript step passed. Recording catalog, import queue, local speech, HUD preferences, storage usage and Engineer Ask guard checks passed. Those checks cover underlying workflows; they do not make the static reference controls functional.

## Shared shell

| Detail | Finding |
|---|---|
| Navigation | The 226 px rail and aligned icon/text columns are retained on all five pages. Rows are 40 px high with a 51 px progression. Active-page highlighting and route navigation work. |
| Brand | The mark and name share a consistent lockup, but the reference's larger, higher logo treatment and descriptor are still visibly different. |
| Top bar/content origin | App top bar is 56 px high and reference content starts at y=65. Mocks use about 60 px and y=74: the compositions begin roughly 9 px early. The browser scrollbar also consumes right-edge space. |
| Typography | Reference compositions use 11 px body text, 12 px panel headings and 21 px page titles; table CSS uses 9 px text and 8 px headers. The references are visibly larger, especially in tables, metric labels and icons. This changes density and leaves extra blank space. |
| Recording badge | Shared header honestly reports UDP status unavailable. Dashboard and Recordings main cards still claim active recording, contradicting that header. |
| Storage | Sidebar always shows 42.6 GB / 200 GB even though measured storage is unavailable. Source: [AppHeader](../web/src/app/AppHeader.tsx). |
| Browser adaptation | Recording Controls correctly navigates to the real control workflow. Browser-inapplicable window symbols are decorative, not operating-system controls; that adaptation should remain explicit. |
| Error/empty states | Five remaining reference compositions do not replace fixture data with unavailable or unselected states. The real workflow below each page does. |

## Dashboard — mock image 1

[Mock](../mock-images/mock-image-1.png) · [Captured page](../artifacts/ui-parity-audit/dashboard-1672x941.jpg)

| Area | Visual parity | Data and interaction parity |
|---|---|---|
| Recording & Session | Overall card order matches. Timer, stop control, current-session thumbnail and text are smaller; thumbnail is 110×64. | **High:** active recording, UDP port, elapsed time, lap, bytes, Monza and date are constants. Stop Recording only navigates to Recordings. |
| Live Telemetry | Gauge treatment is shared with the updated live page. Lower facts/order and scale differ from the dashboard mock. Empty values are appropriate without a pin. | **Pass for source handling:** actual pinned telemetry is used; no pin displays unavailable values. DRS/ERS still lack supported live fields. |
| AI Race Engineer | Prompt rows/composer are taller relative to the card; content exceeds the card's client height by about 7 px. | **High:** local AI readiness is unconditional. Prompt buttons are inert; composer is read-only and Send is disconnected. |
| Lap & Session Selector | Same table headings and selected-row treatment. Small rows require an internal scrollbar instead of showing the mock's eight rows at comparable density. | **High:** laps are literals. Current/Imported tabs and Practice/Monza selectors do not select data. Clicking Imported Sessions produced no change. View all sessions navigation works. |
| Selected Lap Summary | Columns and metric groups exist, but lap/time emphasis is much smaller, context runs together, and the lower card has unused space. | **High:** position, lap/sector times, reference delta, tyre estimates, fuel litres, ERS and temperatures are constants. Set as Reference and overflow controls are inert. |
| Track & Region Analysis | Fixed map is much smaller inside the card; the raster contains tiny labels and an unrelated embedded legend/control strip. | **High:** an image supplies the Monza shape and finding; circuit selector is inert. It does not use the new dynamic track component. |
| Recent Recordings | Panel is only about 150 px high inside a 208 px row. Internal table scroll clips rows; the mock's panel fills the bottom row. | **High:** filenames, dates, laps and sizes are constants. View Files navigation works; displayed rows do not select captures. |
| Lap Comparison | Panel is also about 150 px high. Header controls wrap, and the fixed chart extends about 38 px below the panel: speed/brake content is clipped. | **High:** chart is a PNG. Session Best, Sectors/Full Lap, channel and settings controls are disconnected. It does not use the new dynamic trace component. |

Sources: [ReferenceScreens](../web/src/app/ReferenceScreens.tsx#L239), recording claims at lines 253–264; static metrics at 407–413; chart/controls at 491–504; [DashboardLiveTelemetry](../web/src/app/DashboardLiveTelemetry.tsx). Real workspace remains under `page.tsx:729`.

## Sessions — mock image 8

[Mock](../mock-images/mock-image-8.png) · [Captured page](../artifacts/ui-parity-audit/sessions-1672x941.jpg)

| Area | Visual parity | Data and interaction parity |
|---|---|---|
| Heading & filters | Broad placement matches; heading/icon and fields are smaller. Main cards start at y=132 versus about y=140. | **High:** Track, Session Type and date controls are inert. Search is read-only. Import Session links to Recordings. |
| Imported Sessions | Correct columns and eight fixture rows, with compressed rows and a large empty lower area. | **High:** all sessions, best laps and statuses are literals; rows do not select real sessions. View all sessions links to the same route. |
| Processing Runs | Broad panel placement matches, with smaller row text and unused lower space. | **High:** IDs, start/end times, sources and completed/processing statuses are literals. All Status is inert. |
| Run Details | Run heading is smaller; View Raw Data falls below the subtitle instead of sitting at the right. Fact grid loses the mock's bordered cells. Lifecycle steps lack the mock's connecting lines. | **High:** selected run, lifecycle timestamps and generated-attempt/evidence counts are constants. Raw-data and overflow actions are inert. |
| Attempts | Columns and eight rows are present, but font, badge and validity marks are smaller. | **High:** lap times, disposition, validity and notes are fixture values; All Laps is inert. |
| Session Summary | Facts collapse into a plain vertical list instead of paired metric cells. | **High:** selected track, weather, length, times and counts are constants. Open Session, View Attempts and Compare Lap are disconnected. Open Session click produced no change. |
| Observation Inventory | Uses repeated file glyphs and grey checks instead of distinct category icons and green availability marks. | **High:** file counts and availability are constants; View All has no inventory action. |
| Archive Status | Plain text replaces bordered fact rows; the panel has substantial unused space. | **High:** path, ZIP name, sizes and archived state are constants. Folder/download icons do not operate on an archive. |

Sources: [ReferenceScreens](../web/src/app/ReferenceScreens.tsx#L784), fixtures at 808, 832, 862 and 902; actions at 951. Real selected-run browsing and inventory use [SessionsRunEvidence](../web/src/app/SessionsRunEvidence.tsx), behind `sessions/page.tsx:20`.

## AI Engineer — mock image 3

[Mock](../mock-images/mock-image-3.png) · [Captured page](../artifacts/ui-parity-audit/engineer-1672x941.jpg)

| Area | Visual parity | Data and interaction parity |
|---|---|---|
| Title/runtime | Missing the mock's unified white title/intent frame and large blue title icon. Runtime badge is much smaller. | **High:** Local AI/Offline/private-model status is unconditional even when real runtime status is unavailable. |
| Engineer Intent | Four cards are present, with smaller icons/text. | **High:** cards are static containers, not keyboard-operable selectors. Strategy and tyre capabilities are presented without the real workflow's unsupported-state explanation. |
| Conversation | Text, numbered breakdowns, timestamps and takeaway are smaller; boxes differ and the composer sits high with a large blank area below. | **High:** causal losses, braking advice and wheelspin are fixture statements. Listed losses total 0.347 s while the headline says 0.317 s. No recorded evidence establishes those claims. |
| Prompts/composer/feedback | Prompt chips and send controls resemble the reference but have reduced sizes. | **High:** composer is read-only; prompt, send, helpful/unhelpful, copy and overflow buttons are disconnected. Braking points click produced no change. |
| Context & Data Source | Thumbnail is 110×64 instead of the mock's tall crop. Selectors render as small default browser buttons instead of styled full-width fields. | **High:** track/lap/reference/focus are static. Change Context and selectors do not change exact selection. |
| Voice Response | Waveform is small and partly blank; transport controls and durations are smaller. | **High:** Piper/Ready, waveform and duration are fixtures. Controls have no audio action. Real read-aloud uses browser-discovered local voices, with separate preferences. |
| Key Evidence | Metric figures are smaller; map and speed-chart PNGs crop their labels/callouts. | **High:** sectors, map colours and speed differences are raster/literal evidence. Region selectors do not control a trace. |

Sources: [ReferenceScreens](../web/src/app/ReferenceScreens.tsx#L1330), coaching at 1401–1430, controls at 1441–1500. Real [EngineerQueryPanel](../web/src/app/EngineerQueryPanel.tsx) and Ask/microphone workflows sit behind `engineer/page.tsx:273`; supported intents and limitations are explained at line 336.

## Recordings — mock image 6

[Mock](../mock-images/mock-image-6.png) · [Captured page](../artifacts/ui-parity-audit/recordings-1672x941.jpg)

| Area | Visual parity | Data and interaction parity |
|---|---|---|
| Recording & Capture | App strip is about 155 px high versus 207 px in the mock, shifting subsequent content up. Timer/buttons are smaller and too far left; counters are compressed on the right. | **High:** Recording/Listening, port, elapsed time, lap, bytes, packets, drops and rates are constants despite unavailable recording service. Pause/Stop are disconnected. Configure navigation works. |
| Recorded Captures | Header search is unstyled and wraps the import action below it. Table has smaller text/icons and lacks the reference's icon variety. | **High:** search is read-only, import and catalog tabs are inert, rows are fixtures. All Files click produced no change. |
| Replay | Image size is close, but crop removes more cockpit detail. Transport and speed controls are smaller and spacing differs. | **High:** cockpit is an `img`, not video; lap overlay/time/progress are fixed. Replay transport and speed controls do not drive telemetry or video. The real application supports capture telemetry replay, not automatic game-video capture. |
| Import Jobs | Begins at y≈622 versus y≈696; app panel is about 315 px high with excess blank space versus the mock's shorter panel. | **High:** four filenames, percentages and statuses are constants. Import-from-file/overflow actions are inert. |
| Selected Recording Details | Same early start and excess height; quality facts and actions are compressed, with a large middle gap. | **High:** selected capture, quality, loss, sizes and duration are fixtures. Import, Inspect, Replay and Delete are disconnected. |

Sources: [ReferenceScreens](../web/src/app/ReferenceScreens.tsx#L1558), catalog at 1649, image at 1674, job progress at 1710 and actions at 1790. Real recording/import/replay controllers are in [RecordingInbox](../web/src/app/RecordingInbox.tsx), behind `recordings/page.tsx:67`.

## Settings — mock image 5

[Mock](../mock-images/mock-image-5.png) · [Captured page](../artifacts/ui-parity-audit/settings-1672x941.jpg)

| Area | Visual parity | Data and interaction parity |
|---|---|---|
| Heading/banner | Smaller heading/icon and notice text; banner is slightly early and compressed. | **High:** absolute local-processing/privacy claim is static, rather than derived from supported runtime status. Dismiss action is disconnected. |
| Category rail | About 310 px high instead of filling the reference's ≈727 px region. Labels/icons are smaller. | **High:** category buttons do not change active state or navigate. Voice & Audio click produced no change. |
| AI Settings | First-row card is ≈219 px high versus ≈308 px; toggle arrangement, field widths and density differ. | **High:** Llama 3.1 selection conflicts with the real workflow's pinned Qwen3 4B/Ollama profile. Manage Models, personality, response length and switches are disconnected. |
| Voice & Audio | Also ≈221 px high versus ≈308 px. Test button, microphone bars and sliders differ in scale/arrangement. | **High:** Windows device, Brian voice, mouse button, meter, speed and volume are fixtures. Actual local read-aloud controls and discovered voice are available in the disclosure. |
| Telemetry | ≈162 px high versus ≈222 px. UDP Port/Queue Size lack numeric steppers. | **High:** Connected, host, port, queue and freshness are constants. Fields are read-only. Real telemetry configuration status is unavailable/read-only. |
| Devices | ≈247 px high; bottom does not align with Telemetry, so the mock's paired row structure is lost. | **High:** Logitech wheel and mappings are fictitious selections; Detect Devices and dropdowns are disconnected. Real workflow explicitly says device routing/background listening unavailable. |
| Overlay/HUD | ≈233 px high versus ≈180 px; controls stack vertically instead of the compact paired arrangement. | **High:** switches are plain non-focusable `div`s. Values do not reflect persisted browser HUD preferences. The actual HUD is a browser tab, not an always-on-top game overlay. |
| Storage | ≈158 px high; disk legend becomes a horizontal wrap instead of the reference's rows. | **High:** Alex's paths, total/capacity and category sizes are literals. Inputs are read-only; cleanup/folder actions are inert. Real measured usage currently shows unavailable. |
| Footer actions | Similar right alignment but early, with different row padding and sizes. | **High:** Apply Settings, Cancel and Reset to Defaults have no handlers or form submission. Real voice/HUD controls each have their own validated save/reset logic. |

Sources: [ReferenceScreens](../web/src/app/ReferenceScreens.tsx#L1831), static values at 1857, 1912, 2041, 2081 and 2101; footer at 2127. Real [Settings route](../web/src/app/settings/page.tsx#L38) was expanded and inspected: Qwen3 runtime status unavailable, local browser voice available, telemetry/storage unavailable, and general/device limitations explicit.

## Recommended implementation order

1. **Replace fabricated operation/status data first:** Dashboard/Recordings recording state and shared storage; use existing controllers with honest unselected/error states.
2. **Connect the existing real workflows to their visible controls:** recording/catalog/import/replay, exact session/run/attempt selections, voice/HUD preferences, telemetry/storage/runtime status and supported Engineer intents.
3. **Reuse the new dynamic analysis widgets on Dashboard:** selected region map and comparison traces, retaining reference/model/mask validation.
4. **Correct shell typography and geometry, then each panel:** top inset, table density, recording strip height, settings row alignment, image crops, chart clipping and default-button styles. Repeat at 1672×941 and narrower web layouts.
5. **Acceptance:** every displayed measurement must come from the selected service/report; every visible action must work or have an explicit unavailable state. Keep draft geometry/coaching and browser/native capability qualifications.

These are follow-up implementation tasks identified by the audit. Passing source guard tests alone is insufficient for UI parity acceptance.
