"use client";

import { useEffect, useState, type ReactNode } from "react";
import type {
  LiveCarDamageRecord,
  LiveCarSetupRecord,
  LiveCarStatusRecord,
  LiveLapTimingRecord,
  LiveSessionConditionsRecord,
  LiveTelemetryRecord,
} from "@/lib/api";
import {
  readPinnedLiveCurrent,
  type LiveSource,
  type PinnedLiveSnapshot,
} from "@/lib/live";
import {
  LiveCarDamagePanel,
  LiveCarSetupPanel,
  LiveSessionConditionsPanel,
  LiveCarStatusPanel,
  LiveLapTimingPanel,
  LiveTelemetryPanel,
} from "./RecordingControls";

type Status = "ready" | "unavailable" | "replaced" | "error" | "unselected" | "invalid";
type MonitorRecord =
  | LiveTelemetryRecord
  | LiveCarStatusRecord
  | LiveLapTimingRecord
  | LiveCarDamageRecord
  | LiveCarSetupRecord
  | LiveSessionConditionsRecord;

const activeRecordingStates = new Set(["starting", "recording", "stopping"]);
const activeReplayStates = new Set([
  "starting",
  "playing",
  "pausing",
  "paused",
  "resuming",
  "stepping",
  "stopping",
]);

export default function LiveTelemetryView({
  initialStatus,
  initialSnapshot,
  source,
  operationId,
  recordingsHref,
}: {
  initialStatus: Status;
  initialSnapshot: PinnedLiveSnapshot | null;
  source: LiveSource | null;
  operationId: string | null;
  recordingsHref: string | null;
}) {
  const [status, setStatus] = useState<Status>(initialStatus);
  const [snapshot, setSnapshot] = useState<PinnedLiveSnapshot | null>(initialSnapshot);

  useEffect(() => {
    if (
      !source ||
      !operationId ||
      (initialStatus !== "ready" && initialStatus !== "error") ||
      (initialSnapshot && !isActive(initialSnapshot))
    ) {
      return;
    }

    let mounted = true;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | null = null;

    const poll = async () => {
      const requestController = new AbortController();
      controller = requestController;
      let deadlineExpired = false;
      const deadline = setTimeout(() => {
        deadlineExpired = true;
        requestController.abort();
      }, 5000);
      try {
        const endpoint =
          source === "recording"
            ? "/api/recordings/current"
            : "/api/replays/current";
        const response = await fetch(endpoint, {
          cache: "no-store",
          signal: requestController.signal,
        });
        const body: unknown = await response.json();
        if (!response.ok) throw new Error("live_source_unavailable");
        const read = readPinnedLiveCurrent(source, operationId, body);
        if (!mounted) return;
        if (read.status === "ready") {
          setStatus("ready");
          setSnapshot(read.snapshot);
          if (isActive(read.snapshot)) timer = setTimeout(poll, 500);
          return;
        }
        if (read.status === "error") {
          setStatus("error");
          setSnapshot(null);
          timer = setTimeout(poll, 1000);
          return;
        }
        setStatus(read.status);
        setSnapshot(null);
      } catch {
        if (!mounted || (requestController.signal.aborted && !deadlineExpired))
          return;
        setStatus("error");
        setSnapshot(null);
        timer = setTimeout(poll, 1000);
      } finally {
        clearTimeout(deadline);
      }
    };

    timer = setTimeout(poll, 500);
    return () => {
      mounted = false;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [initialSnapshot, initialStatus, operationId, source]);

  const ended = snapshot !== null && !isActive(snapshot);

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
            <span className={`recording-light ${isActive(snapshot) ? "recording-light-active" : ""}`} />
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

function isActive(snapshot: PinnedLiveSnapshot) {
  return snapshot.source === "recording"
    ? activeRecordingStates.has(snapshot.state)
    : activeReplayStates.has(snapshot.state);
}

function readable(value: string | null | undefined) {
  return value ? value.replaceAll("_", " ").toUpperCase() : "Unknown";
}

