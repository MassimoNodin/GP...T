import type { LiveLapTimingRecord, LiveTelemetryRecord } from "@/lib/api";
import type { PinnedLiveSnapshot } from "@/lib/live";

export interface HudDisplayField {
  label: string;
  value: string;
}

export interface HudDisplayGroup {
  status: string;
  age: string;
  source: string;
  fields: HudDisplayField[];
}

export interface HudDisplayModel {
  telemetry: HudDisplayGroup;
  lapTiming: HudDisplayGroup;
}

export function projectHudSnapshot(
  snapshot: PinnedLiveSnapshot | null,
): HudDisplayModel {
  const telemetry = snapshot?.live_telemetry ?? null;
  const lapTiming = snapshot?.live_lap_timing ?? null;
  const showTelemetryValues =
    telemetry?.status === "fresh" && hasTelemetryIdentity(telemetry);
  const telemetryStatus =
    telemetry?.status === "fresh" && !showTelemetryValues
      ? "identity unavailable"
      : (telemetry?.status ?? "unavailable");
  const timingStatus = lapTimingStatus(telemetry, lapTiming);
  const showTimingValues = timingStatus === "fresh";
  const sourceEpoch = validSourceEpoch(telemetry?.source_epoch)
    ? telemetry.source_epoch
    : null;

  return {
    telemetry: {
      status: telemetryStatus,
      age: ageLabel(telemetry?.age_ms ?? null),
      source: sourceEpoch
        ? `EPOCH ${sourceEpoch.slice(0, 8)}`
        : "EPOCH UNKNOWN",
      fields: [
        metric(
          "SPEED",
          showTelemetryValues ? telemetry : null,
          showTelemetryValues ? telemetry?.speed_kph : null,
          isSpeed,
          (value) => `${Math.round(value)} km/h`,
        ),
        metric(
          "GEAR",
          showTelemetryValues ? telemetry : null,
          showTelemetryValues ? telemetry?.gear : null,
          isGear,
          (value) => (value === -1 ? "R" : String(value)),
        ),
        metric(
          "RPM",
          showTelemetryValues ? telemetry : null,
          showTelemetryValues ? telemetry?.engine_rpm : null,
          isRpm,
          (value) => Math.round(value).toLocaleString(),
        ),
        metric(
          "THROTTLE",
          showTelemetryValues ? telemetry : null,
          showTelemetryValues ? telemetry?.throttle : null,
          isPedal,
          (value) => `${Math.round(value * 100)}%`,
        ),
        metric(
          "BRAKE",
          showTelemetryValues ? telemetry : null,
          showTelemetryValues ? telemetry?.brake : null,
          isPedal,
          (value) => `${Math.round(value * 100)}%`,
        ),
      ],
    },
    lapTiming: {
      status: timingStatus,
      age: ageLabel(lapTiming?.age_ms ?? null),
      source: identityLabel(lapTiming),
      fields: [
        metric(
          "LAP",
          showTimingValues ? lapTiming : null,
          showTimingValues ? lapTiming?.lap_number : null,
          isLapNumber,
          (value) => String(value),
        ),
        metric(
          "LAP CLOCK",
          showTimingValues ? lapTiming : null,
          showTimingValues ? lapTiming?.current_lap_time_ms : null,
          isLapClock,
          formatLapClock,
        ),
      ],
    },
  };
}

function metric(
  label: string,
  group: LiveTelemetryRecord | LiveLapTimingRecord | null,
  value: unknown,
  validate: (value: unknown) => value is number,
  format: (value: number) => string,
): HudDisplayField {
  return {
    label,
    value: group?.status === "fresh" && validate(value) ? format(value) : "—",
  };
}

function lapTimingStatus(
  telemetry: LiveTelemetryRecord | null,
  timing: LiveLapTimingRecord | null,
): string {
  if (!timing) return "unavailable";
  if (timing.status !== "fresh") return timing.status;
  if (!hasLapTimingIdentity(timing)) return "identity unavailable";
  if (
    telemetry &&
    hasSharedIdentity(telemetry) &&
    (telemetry.session_uid !== timing.session_uid ||
      telemetry.packet_format !== timing.packet_format ||
      telemetry.player_car_index !== timing.player_car_index)
  ) {
    return "identity mismatch";
  }
  return "fresh";
}

function hasTelemetryIdentity(value: LiveTelemetryRecord): boolean {
  return hasSharedIdentity(value) && validSourceEpoch(value.source_epoch);
}

function hasSharedIdentity(
  value: LiveTelemetryRecord | LiveLapTimingRecord,
): boolean {
  return (
    isSessionUid(value.session_uid) &&
    isPacketFormat(value.packet_format) &&
    isPlayerIndex(value.player_car_index)
  );
}

function hasLapTimingIdentity(value: LiveLapTimingRecord): boolean {
  return hasSharedIdentity(value);
}

function isSessionUid(value: unknown): value is string {
  return typeof value === "string" && /^\d{1,20}$/.test(value);
}

function isPacketFormat(value: unknown): value is 2025 | 2026 {
  return value === 2025 || value === 2026;
}

function isPlayerIndex(value: unknown): value is number {
  return (
    Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 23
  );
}

function validSourceEpoch(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(value);
}

function identityLabel(value: LiveLapTimingRecord | null) {
  if (!value) return "PLAYER / SESSION UNKNOWN";
  const player = value.player_car_index;
  const session = value.session_uid;
  const format = value.packet_format;
  return [
    isPlayerIndex(player) ? `PLAYER ${player + 1}` : "PLAYER ?",
    isSessionUid(session) ? `SESSION ${session}` : "SESSION ?",
    isPacketFormat(format) ? `F${format}` : "FORMAT ?",
  ].join(" · ");
}

function ageLabel(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? `AGE ${(value / 1000).toFixed(1)} s`
    : "AGE unavailable";
}

function isSpeed(value: unknown): value is number {
  return rangedFinite(value, 0, 500);
}

function isGear(value: unknown): value is number {
  return (
    Number.isInteger(value) && (value as number) >= -1 && (value as number) <= 8
  );
}

function isRpm(value: unknown): value is number {
  return (
    Number.isInteger(value) &&
    (value as number) >= 0 &&
    (value as number) <= 65_535
  );
}

function isPedal(value: unknown): value is number {
  return rangedFinite(value, 0, 1);
}

function isLapNumber(value: unknown): value is number {
  return (
    Number.isInteger(value) &&
    (value as number) >= 1 &&
    (value as number) <= 255
  );
}

function isLapClock(value: unknown): value is number {
  return (
    Number.isInteger(value) &&
    (value as number) >= 1 &&
    (value as number) <= 0xffff_ffff
  );
}

function rangedFinite(
  value: unknown,
  minimum: number,
  maximum: number,
): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= minimum &&
    value <= maximum
  );
}

function formatLapClock(value: number) {
  const minutes = Math.floor(value / 60_000);
  const seconds = ((value % 60_000) / 1000).toFixed(3).padStart(6, "0");
  return `${minutes}:${seconds}`;
}
