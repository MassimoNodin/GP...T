"use client";

import type { ReactNode } from "react";
import type {
  LiveCarDamageRecord,
  LiveCarSetupRecord,
  LiveCarStatusRecord,
  LiveLapTimingRecord,
  LiveMotionRecord,
  LiveSessionConditionsRecord,
  LiveSessionHistoryRecord,
  LiveTelemetryRecord,
} from "@/lib/api";
import {
  isPinnedLiveActive,
  type LiveSource,
  type PinnedLiveSnapshot,
} from "@/lib/live";
import {
  usePinnedLiveCurrent,
  type PinnedLiveViewStatus,
} from "@/lib/use-pinned-live-current";
import {
  LiveCarDamagePanel,
  LiveCarSetupPanel,
  LiveMotionPanel,
  LiveSessionConditionsPanel,
  LiveSessionHistoryPanel,
  LiveCarStatusPanel,
  LiveLapTimingPanel,
  LiveTelemetryPanel,
} from "./RecordingControls";
import LiveTelemetryChart from "./LiveTelemetryChart";

type MonitorRecord =
  | LiveTelemetryRecord
  | LiveCarStatusRecord
  | LiveLapTimingRecord
  | LiveCarDamageRecord
  | LiveCarSetupRecord
  | LiveSessionConditionsRecord
  | LiveMotionRecord
  | LiveSessionHistoryRecord;

export default function LiveTelemetryView({
  initialStatus,
  initialSnapshot,
  source,
  operationId,
  recordingsHref,
  hudHref,
}: {
  initialStatus: PinnedLiveViewStatus;
  initialSnapshot: PinnedLiveSnapshot | null;
  source: LiveSource | null;
  operationId: string | null;
  recordingsHref: string | null;
  hudHref: string | null;
}) {
  const { status, snapshot } = usePinnedLiveCurrent({
    initialStatus,
    initialSnapshot,
    source,
    operationId,
  });
  const ended = snapshot !== null && !isPinnedLiveActive(snapshot);

  return (
    <section className="live-source-panel" aria-label="Pinned live telemetry">
      {status === "unselected" ? (
        <div className="live-selection-message">
          <strong>Choose a live source from its controls.</strong>
          <p>
            Start a recording or replay on the Recordings page, then open its
            live telemetry. This page does not select the current operation on
            its own.
          </p>
          <OpenRecordingsLink href={recordingsHref} />
        </div>
      ) : status === "invalid" ? (
        <div className="live-selection-message" role="status">
          <strong>This live source link is incomplete or malformed.</strong>
          <p>Return to the recording or replay controls and open it again.</p>
          <OpenRecordingsLink href={recordingsHref} />
        </div>
      ) : status === "replaced" ? (
        <div className="live-selection-message" role="status">
          <strong>The pinned operation has been replaced.</strong>
          <p>
            Its telemetry was cleared. Open the live view again from the active
            recording or replay controls to follow a different operation.
          </p>
          <OpenRecordingsLink href={recordingsHref} />
        </div>
      ) : status === "unavailable" ? (
        <div className="live-selection-message" role="status">
          <strong>The pinned operation is no longer available.</strong>
          <p>
            No other operation was selected. Return to the controls to open a
            new recording or replay.
          </p>
          <OpenRecordingsLink href={recordingsHref} />
        </div>
      ) : status === "error" ? (
        <div className="live-selection-message" role="status">
          <strong>The pinned source could not be refreshed.</strong>
          <p>Its telemetry is hidden while the read-only status check retries.</p>
          <OpenRecordingsLink href={recordingsHref} />
        </div>
      ) : snapshot ? (
        <>
          <div className="live-source-heading">
            <div>
              <div className="eyebrow">
                {snapshot.source === "replay" ? "PINNED DIAGNOSTIC REPLAY" : "PINNED UDP RECORDING"}
              </div>
              <h2>
                {snapshot.source === "replay"
                  ? snapshot.capture_name ?? "Capture replay"
                  : "Current recording"}
              </h2>
            </div>
            <div className="live-source-actions">
              {isPinnedLiveActive(snapshot) && hudHref ? (
                <a
                  className="live-open-link"
                  href={hudHref}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open browser HUD <span aria-hidden="true">↗</span>
                </a>
              ) : null}
              <span
                className={
                  "recording-light " +
                  (isPinnedLiveActive(snapshot) ? "recording-light-active" : "")
                }
              />
            </div>
          </div>
          <div className="live-source-facts">
            <LiveFact label="OPERATION" value={snapshot.operation_id} />
            <LiveFact label="STATUS" value={stateLabel(snapshot)} />
            {snapshot.source === "replay" && snapshot.speed !== null && (
              <LiveFact label="PLAYBACK SPEED" value={`${snapshot.speed}×`} />
            )}
            <LiveFact label="TRACK" value={snapshot.context?.track_name ?? "Unknown"} />
            <LiveFact label="SESSION" value={readable(snapshot.context?.session_type)} />
            <LiveFact label="MODE" value={readable(snapshot.context?.game_mode)} />
          </div>

          {ended ? (
            <p className="live-operation-ended" role="status">
              This operation has ended. Live monitor values have been cleared.
            </p>
          ) : (
            <div className="live-monitor-list">
              <MonitorGroup
                title="Player telemetry"
                telemetry={snapshot.live_telemetry}
                sourceKind={snapshot.source}
              >
                {snapshot.live_telemetry ? (
                  <LiveTelemetryPanel
                    telemetry={snapshot.live_telemetry}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
              {snapshot.live_telemetry ? (
                <div className="live-monitor-group">
                  <LiveTelemetryChart
                    key={[
                      snapshot.source,
                      snapshot.operation_id,
                      snapshot.live_telemetry.source_epoch ?? "missing-epoch",
                      snapshot.live_telemetry.session_uid ?? "missing-session",
                      snapshot.live_telemetry.packet_format ?? "missing-format",
                      snapshot.live_telemetry.player_car_index ?? "missing-player",
                    ].join(":")}
                    telemetry={snapshot.live_telemetry}
                    sourceState={snapshot.state}
                  />
                </div>
              ) : null}
              <MonitorGroup
                title="Car status"
                telemetry={snapshot.live_car_status}
                sourceKind={snapshot.source}
              >
                {snapshot.live_car_status ? (
                  <LiveCarStatusPanel
                    telemetry={snapshot.live_car_status}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
              <MonitorGroup
                title="Lap timing"
                telemetry={snapshot.live_lap_timing}
                sourceKind={snapshot.source}
              >
                {snapshot.live_lap_timing ? (
                  <LiveLapTimingPanel
                    telemetry={snapshot.live_lap_timing}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
              <MonitorGroup
                title="Sparse Car Damage observation"
                telemetry={snapshot.live_car_damage}
                sourceKind={snapshot.source}
              >
                {snapshot.live_car_damage ? (
                  <LiveCarDamagePanel
                    telemetry={snapshot.live_car_damage}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
              <MonitorGroup
                title="Sparse Car Setups observation"
                telemetry={snapshot.live_car_setup}
                sourceKind={snapshot.source}
              >
                {snapshot.live_car_setup ? (
                  <LiveCarSetupPanel
                    telemetry={snapshot.live_car_setup}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
              <MonitorGroup
                title="Session-wide conditions"
                telemetry={snapshot.live_session_conditions}
                sourceKind={snapshot.source}
              >
                {snapshot.live_session_conditions ? (
                  <LiveSessionConditionsPanel
                    telemetry={snapshot.live_session_conditions}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
              <MonitorGroup
                title="Selected-player Motion"
                telemetry={snapshot.live_motion}
                sourceKind={snapshot.source}
              >
                {snapshot.live_motion ? (
                  <LiveMotionPanel
                    telemetry={snapshot.live_motion}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
              <MonitorGroup
                title="Game-reported lap history"
                telemetry={snapshot.live_session_history}
                sourceKind={snapshot.source}
              >
                {snapshot.live_session_history ? (
                  <LiveSessionHistoryPanel
                    telemetry={snapshot.live_session_history}
                    sourceKind={snapshot.source}
                  />
                ) : null}
              </MonitorGroup>
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}

function MonitorGroup({
  title,
  telemetry,
  sourceKind,
  children,
}: {
  title: string;
  telemetry: MonitorRecord | null;
  sourceKind: LiveSource;
  children: ReactNode;
}) {
  return (
    <div className="live-monitor-group">
      <div className="live-monitor-meta">
        <strong>{title}</strong>
        {telemetry ? (
          <div>
            <span>SESSION {telemetry.session_uid ?? "—"}</span>
            {"player_car_index" in telemetry ? (
              <span>PLAYER {telemetry.player_car_index ?? "—"}</span>
            ) : null}
            <span>FORMAT {telemetry.packet_format ?? "—"}</span>
            <span>FRAME {telemetry.frame_identifier ?? "—"}</span>
            <span>AGE {formatAge(telemetry.age_ms, sourceKind)}</span>
          </div>
        ) : (
          <span className="live-monitor-unavailable">
            This optional monitor group is unavailable for the pinned source.
          </span>
        )}
      </div>
      {telemetry ? (
        children
      ) : (
        <div className="live-monitor-unavailable-panel">Monitor unavailable</div>
      )}
    </div>
  );
}

function LiveFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="live-source-fact">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function OpenRecordingsLink({ href }: { href: string | null }) {
  return href ? (
    <a className="live-open-link" href={href}>
      Open recording controls <span aria-hidden="true">↗</span>
    </a>
  ) : null;
}

function formatAge(value: number | null, source: LiveSource) {
  if (value === null || !Number.isFinite(value)) return "unknown";
  const delivery = source === "replay" ? " delivery" : "";
  return `${(value / 1000).toFixed(1)}s${delivery}`;
}

function stateLabel(snapshot: PinnedLiveSnapshot) {
  const readableState = snapshot.state.replaceAll("_", " ");
  return snapshot.source === "replay"
    ? `Replay ${readableState}`
    : `Recording ${readableState}`;
}

function readable(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ").toUpperCase() : "Unknown";
}
