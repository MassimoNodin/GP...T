"use client";

import { useSyncExternalStore, type CSSProperties } from "react";
import {
  getLocalHudPreferenceSnapshot,
  getServerLocalHudPreferenceSnapshot,
  subscribeToLocalHudPreferences,
} from "@/lib/local-hud-preferences";
import {
  isPinnedLiveActive,
  type LiveSource,
  type PinnedLiveSnapshot,
} from "@/lib/live";
import {
  usePinnedLiveCurrent,
  type PinnedLiveViewStatus,
} from "@/lib/use-pinned-live-current";
import { projectHudSnapshot } from "@/lib/hud-display";

export default function BrowserHud({
  initialStatus,
  initialSnapshot,
  source,
  operationId,
  liveHref,
}: {
  initialStatus: PinnedLiveViewStatus;
  initialSnapshot: PinnedLiveSnapshot | null;
  source: LiveSource | null;
  operationId: string | null;
  liveHref: string | null;
}) {
  const { status, snapshot } = usePinnedLiveCurrent({
    initialStatus,
    initialSnapshot,
    source,
    operationId,
  });
  const preferences = useSyncExternalStore(
    subscribeToLocalHudPreferences,
    getLocalHudPreferenceSnapshot,
    getServerLocalHudPreferenceSnapshot,
  );
  const display = projectHudSnapshot(snapshot);
  const active = snapshot !== null && isPinnedLiveActive(snapshot);
  const style = {
    "--hud-opacity": String(preferences.effective.background_opacity),
    "--hud-base-font-size": `${16 * preferences.effective.text_scale}px`,
  } as CSSProperties;

  return (
    <main className="browser-hud-page" id="main-content">
      <header className="browser-hud-topbar">
        <a className="browser-hud-brand" href={liveHref ?? "/recordings"}>
          <span className="brand-name">
            GP<span className="brand-dots">...</span>T
          </span>
        </a>
        <span className="browser-hud-label">BROWSER HUD · PINNED SOURCE</span>
        {liveHref ? (
          <a className="browser-hud-back" href={liveHref}>
            Back to live <span aria-hidden="true">↗</span>
          </a>
        ) : null}
      </header>

      <section
        className={
          "browser-hud-stage hud-align-" + preferences.effective.alignment
        }
        data-theme={preferences.effective.theme}
        style={style}
        aria-label="Browser HUD display"
      >
        {active && snapshot ? (
          <article className="browser-hud-panel">
            <header className="browser-hud-source">
              <div>
                <span className="eyebrow">
                  {snapshot.source === "replay"
                    ? "CAPTURE REPLAY"
                    : "UDP RECORDING"}
                </span>
                <h1>
                  {snapshot.source === "replay"
                    ? (snapshot.capture_name ?? "Pinned replay")
                    : "Current recording"}
                </h1>
                <p>
                  {snapshot.source === "replay" && snapshot.state === "paused"
                    ? "REPLAY PAUSED · delivery age continues"
                    : stateLabel(snapshot)}
                  {" · "}
                  OP {snapshot.operation_id.slice(0, 8)}
                </p>
              </div>
              <span className="recording-light recording-light-active" />
            </header>

            <HudReadingGroup
              title="PLAYER TELEMETRY"
              reading={display.telemetry}
            />
            <HudReadingGroup
              title="GAME LAP TIMING"
              reading={display.lapTiming}
            />
            <p className="browser-hud-disclaimer">
              Browser display only · No always-on-top game overlay
            </p>
          </article>
        ) : (
          <div className="browser-hud-state" role="status">
            <strong>{statusHeading(status, snapshot)}</strong>
            <p>{statusDescription(status, snapshot)}</p>
            {liveHref ? (
              <a className="live-open-link" href={liveHref}>
                Return to this pinned live source
              </a>
            ) : (
              <a className="live-open-link" href="/recordings">
                Open recording controls
              </a>
            )}
          </div>
        )}
      </section>
    </main>
  );
}

function HudReadingGroup({
  title,
  reading,
}: {
  title: string;
  reading: ReturnType<typeof projectHudSnapshot>["telemetry"];
}) {
  return (
    <section className="browser-hud-group" aria-label={title}>
      <header>
        <strong>{title}</strong>
        <span
          className={
            "hud-reading-state hud-state-" + reading.status.replaceAll(" ", "-")
          }
        >
          {reading.status.replaceAll("_", " ").toUpperCase()}
        </span>
      </header>
      <div className="browser-hud-group-meta">
        <span>{reading.age}</span>
        <span>{reading.source}</span>
      </div>
      <dl>
        {reading.fields.map((field) => (
          <div className="browser-hud-metric" key={field.label}>
            <dt>{field.label}</dt>
            <dd>{field.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function stateLabel(snapshot: PinnedLiveSnapshot) {
  return snapshot.source === "replay"
    ? "REPLAY " + snapshot.state.replaceAll("_", " ").toUpperCase()
    : "RECORDING " + snapshot.state.replaceAll("_", " ").toUpperCase();
}

function statusHeading(
  status: PinnedLiveViewStatus,
  snapshot: PinnedLiveSnapshot | null,
) {
  if (snapshot && !isPinnedLiveActive(snapshot))
    return "The pinned operation has ended.";
  if (status === "suspended") return "HUD reads are suspended.";
  if (status === "replaced") return "The pinned operation was replaced.";
  if (status === "ready") return "Refreshing the pinned operation.";
  if (status === "unavailable") return "The pinned operation is unavailable.";
  if (status === "error") return "The pinned source could not be refreshed.";
  if (status === "invalid") return "This HUD link is incomplete or malformed.";
  return "Choose a pinned source from its active controls.";
}

function statusDescription(
  status: PinnedLiveViewStatus,
  snapshot: PinnedLiveSnapshot | null,
) {
  if (snapshot && !isPinnedLiveActive(snapshot))
    return "HUD telemetry was cleared. Open the HUD again from the active operation controls.";
  if (status === "suspended")
    return "This browser tab is hidden. Reads resume for the same operation when it becomes visible.";
  if (status === "replaced" || status === "unavailable")
    return "No other recording or replay was selected. Return to the controls to open an exact source.";
  if (status === "ready")
    return "No values are shown while the exact pinned source refreshes.";
  if (status === "error")
    return "Displayed telemetry is cleared while the pinned source status retries.";
  if (status === "invalid")
    return "A single recording or replay and its exact operation ID are required. No current operation was read.";
  return "Start or resume a recording or replay, then open its HUD from the controls.";
}
