import type {
  LiveCarDamageRecord,
  LiveCarSetupRecord,
  LiveCarStatusRecord,
  LiveLapTimingRecord,
  LiveMotionRecord,
  LiveSessionConditionsRecord,
  LiveSessionHistoryLapRecord,
  LiveSessionHistoryRecord,
  LiveTelemetryRecord,
  SessionContext,
} from "@/lib/api";

export type LiveSource = "recording" | "replay";

export interface PinnedLiveSnapshot {
  source: LiveSource;
  operation_id: string;
  state: string;
  context: SessionContext | null;
  live_telemetry: LiveTelemetryRecord | null;
  live_car_status: LiveCarStatusRecord | null;
  live_lap_timing: LiveLapTimingRecord | null;
  live_car_damage: LiveCarDamageRecord | null;
  live_car_setup: LiveCarSetupRecord | null;
  live_session_conditions: LiveSessionConditionsRecord | null;
  live_motion: LiveMotionRecord | null;
  live_session_history: LiveSessionHistoryRecord | null;
  capture_name: string | null;
  speed: number | null;
}

export type PinnedLiveRead =
  | { status: "ready"; snapshot: PinnedLiveSnapshot }
  | { status: "unavailable" | "replaced" | "error"; snapshot: null };

const monitorStates = new Set([
  "waiting",
  "fresh",
  "stale",
  "unsupported",
  "unavailable",
]);
const recordingStates = new Set([
  "starting",
  "recording",
  "stopping",
  "complete",
  "failed",
  "interrupted",
]);
const replayStates = new Set([
  "starting",
  "playing",
  "pausing",
  "paused",
  "resuming",
  "stepping",
  "stopping",
  "stopped",
  "completed",
  "failed",
]);

export function readPinnedLiveCurrent(
  source: LiveSource,
  operationId: string,
  value: unknown,
): PinnedLiveRead {
  if (!isRecord(value) || value.status !== "ok") {
    return { status: "error", snapshot: null };
  }
  if (!("data" in value)) return { status: "error", snapshot: null };
  if (value.data === null) return { status: "unavailable", snapshot: null };
  if (!isRecord(value.data)) return { status: "error", snapshot: null };

  const data = value.data;
  const returnedId =
    source === "recording" ? data.recording_id : data.playback_id;
  if (typeof returnedId !== "string") {
    return { status: "error", snapshot: null };
  }
  if (returnedId !== operationId) {
    return { status: "replaced", snapshot: null };
  }

  const state = source === "recording" ? data.status : data.state;
  const validStates = source === "recording" ? recordingStates : replayStates;
  if (typeof state !== "string" || !validStates.has(state)) {
    return { status: "error", snapshot: null };
  }

  const progress = source === "recording" ? asRecord(data.progress) : null;
  const contextValue = source === "recording"
    ? progress?.latest_context
    : data.latest_context;
  const snapshot: PinnedLiveSnapshot = {
    source,
    operation_id: operationId,
    state,
    context: isRecord(contextValue)
      ? (contextValue as SessionContext)
      : null,
    live_telemetry: readLiveTelemetryMonitor(
      source === "recording"
        ? progress?.live_telemetry
        : data.live_telemetry,
    ),
    live_car_status: readMonitor(
      source === "recording"
        ? progress?.live_car_status
        : data.live_car_status,
    ) as LiveCarStatusRecord | null,
    live_lap_timing: readMonitor(
      source === "recording"
        ? progress?.live_lap_timing
        : data.live_lap_timing,
    ) as LiveLapTimingRecord | null,
    live_car_damage: readCarDamageMonitor(
      source === "recording"
        ? progress?.live_car_damage
        : data.live_car_damage,
    ),
    live_car_setup: readCarSetupMonitor(
      source === "recording"
        ? progress?.live_car_setup
        : data.live_car_setup,
    ),
    live_session_conditions: readSessionConditionsMonitor(
      source === "recording"
        ? progress?.live_session_conditions
        : data.live_session_conditions,
    ),
    live_motion: readMotionMonitor(
      source === "recording"
        ? progress?.live_motion
        : data.live_motion,
    ),
    live_session_history: readSessionHistoryMonitor(
      source === "recording"
        ? progress?.live_session_history
        : data.live_session_history,
    ),
    capture_name:
      source === "replay" && typeof data.capture_name === "string"
        ? data.capture_name
        : null,
    speed:
      source === "replay" &&
      typeof data.speed === "number" &&
      Number.isFinite(data.speed)
        ? data.speed
        : null,
  };
  return { status: "ready", snapshot };
}

export function readCarDamageMonitor(value: unknown): LiveCarDamageRecord | null {
  const monitor = readMonitor(value);
  if (!monitor || !isRecord(value)) return null;
  return {
    ...(monitor as LiveCarDamageRecord),
    reason:
      typeof value.reason === "string" && value.reason.length <= 80
        ? value.reason
        : value.reason === null
          ? null
          : "malformed_optional_fields",
    age_ms:
      typeof value.age_ms === "number" &&
      Number.isFinite(value.age_ms) &&
      value.age_ms >= 0
        ? value.age_ms
        : null,
    observation_count:
      Number.isSafeInteger(value.observation_count) &&
      (value.observation_count as number) >= 0 &&
      (value.observation_count as number) <= 2_147_483_647
        ? (value.observation_count as number)
        : undefined,
    session_uid:
      typeof value.session_uid === "string" ? value.session_uid : null,
    frame_identifier: boundedInteger(value.frame_identifier),
    packet_format: boundedInteger(value.packet_format),
    player_car_index: boundedInteger(value.player_car_index),
    session_time_s: finiteNumber(value.session_time_s),
    tyre_wear_percent: percentArray(value.tyre_wear_percent, false),
    tyre_damage_percent: percentArray(value.tyre_damage_percent, true),
    brake_damage_percent: percentArray(value.brake_damage_percent, true),
    front_left_wing_damage_percent: percent(value.front_left_wing_damage_percent, true),
    front_right_wing_damage_percent: percent(value.front_right_wing_damage_percent, true),
    rear_wing_damage_percent: percent(value.rear_wing_damage_percent, true),
    engine_damage_percent: percent(value.engine_damage_percent, true),
    validation_flags:
      Array.isArray(value.validation_flags) &&
      value.validation_flags.length <= 16 &&
      value.validation_flags.every(
        (flag) => typeof flag === "string" && flag.length <= 80,
      )
        ? value.validation_flags
        : [],
  };
}

const carSetupIntegerFields = [
  "front_wing",
  "rear_wing",
  "on_throttle_differential",
  "off_throttle_differential",
  "front_suspension",
  "rear_suspension",
  "front_anti_roll_bar",
  "rear_anti_roll_bar",
  "front_suspension_height",
  "rear_suspension_height",
  "brake_pressure_percent",
  "brake_bias_percent",
  "engine_braking_percent",
  "ballast",
] as const;

const carSetupFloatFields = [
  "front_camber",
  "rear_camber",
  "front_toe",
  "rear_toe",
  "rear_left_tyre_pressure_psi",
  "rear_right_tyre_pressure_psi",
  "front_left_tyre_pressure_psi",
  "front_right_tyre_pressure_psi",
  "fuel_load",
  "next_front_wing_value",
] as const;

export function readCarSetupMonitor(value: unknown): LiveCarSetupRecord | null {
  const monitor = readMonitor(value);
  if (!monitor || !isRecord(value)) return null;
  const fields: Record<string, number | null> = {};
  for (const field of carSetupIntegerFields) {
    fields[field] = wireInteger(value[field]);
  }
  for (const field of carSetupFloatFields) {
    fields[field] = finiteNumber(value[field]);
  }
  return {
    ...(monitor as LiveCarSetupRecord),
    reason:
      typeof value.reason === "string" && value.reason.length <= 80
        ? value.reason
        : value.reason === null
          ? null
          : "malformed_optional_fields",
    age_ms:
      typeof value.age_ms === "number" &&
      Number.isFinite(value.age_ms) &&
      value.age_ms >= 0
        ? value.age_ms
        : null,
    observation_count:
      Number.isSafeInteger(value.observation_count) &&
      (value.observation_count as number) >= 0 &&
      (value.observation_count as number) <= 2_147_483_647
        ? (value.observation_count as number)
        : undefined,
    session_uid:
      typeof value.session_uid === "string" && value.session_uid.length <= 20
        ? value.session_uid
        : null,
    frame_identifier: boundedInteger(value.frame_identifier),
    packet_format: boundedInteger(value.packet_format),
    player_car_index: boundedInteger(value.player_car_index),
    session_time_s: finiteNumber(value.session_time_s),
    ...fields,
    validation_flags:
      Array.isArray(value.validation_flags) &&
      value.validation_flags.length <= 16 &&
      value.validation_flags.every(
        (flag) => typeof flag === "string" && flag.length <= 80,
      )
        ? value.validation_flags
        : [],
  };
}

export function readSessionConditionsMonitor(
  value: unknown,
): LiveSessionConditionsRecord | null {
  const monitor = readMonitor(value);
  if (!monitor || !isRecord(value)) return null;
  return {
    ...(monitor as LiveSessionConditionsRecord),
    reason:
      typeof value.reason === "string" && value.reason.length <= 80
        ? value.reason
        : value.reason === null
          ? null
          : "malformed_optional_fields",
    age_ms:
      typeof value.age_ms === "number" &&
      Number.isFinite(value.age_ms) &&
      value.age_ms >= 0
        ? value.age_ms
        : null,
    observation_count:
      Number.isSafeInteger(value.observation_count) &&
      (value.observation_count as number) >= 0 &&
      (value.observation_count as number) <= 2_147_483_647
        ? (value.observation_count as number)
        : undefined,
    session_uid:
      typeof value.session_uid === "string" && value.session_uid.length <= 20
        ? value.session_uid
        : null,
    frame_identifier: boundedInteger(value.frame_identifier),
    packet_format: boundedInteger(value.packet_format),
    session_time_s: finiteNumber(value.session_time_s),
    weather_id: wireInteger(value.weather_id),
    weather_name:
      typeof value.weather_name === "string" && value.weather_name.length <= 32
        ? value.weather_name
        : null,
    air_temperature_c: signedWireInteger(value.air_temperature_c),
    track_temperature_c: signedWireInteger(value.track_temperature_c),
    validation_flags:
      Array.isArray(value.validation_flags) &&
      value.validation_flags.length <= 4 &&
      value.validation_flags.every(
        (flag) => typeof flag === "string" && flag.length <= 80,
      )
        ? value.validation_flags
        : [],
  };
}

export function readMotionMonitor(value: unknown): LiveMotionRecord | null {
  const monitor = readMonitor(value);
  if (!monitor || !isRecord(value)) return null;
  return {
    ...(monitor as LiveMotionRecord),
    reason:
      typeof value.reason === "string" && value.reason.length <= 80
        ? value.reason
        : value.reason === null
          ? null
          : "malformed_optional_fields",
    age_ms:
      typeof value.age_ms === "number" &&
      Number.isFinite(value.age_ms) &&
      value.age_ms >= 0
        ? value.age_ms
        : null,
    observation_count:
      Number.isSafeInteger(value.observation_count) &&
      (value.observation_count as number) >= 0 &&
      (value.observation_count as number) <= 2_147_483_647
        ? (value.observation_count as number)
        : undefined,
    session_uid:
      typeof value.session_uid === "string" && value.session_uid.length <= 20
        ? value.session_uid
        : null,
    frame_identifier: boundedInteger(value.frame_identifier),
    packet_format: boundedInteger(value.packet_format),
    player_car_index: boundedInteger(value.player_car_index),
    session_time_s: finiteNumber(value.session_time_s),
    world_position_m: vector3(value.world_position_m),
    world_velocity_mps: vector3(value.world_velocity_mps),
    validation_flags:
      Array.isArray(value.validation_flags) &&
      value.validation_flags.length <= 2 &&
      value.validation_flags.every(
        (flag) => typeof flag === "string" && flag.length <= 80,
      )
        ? value.validation_flags
        : [],
  };
}

export function readSessionHistoryMonitor(
  value: unknown,
): LiveSessionHistoryRecord | null {
  const monitor = readMonitor(value);
  if (!monitor || !isRecord(value)) return null;
  const status = monitor.status;
  const reason =
    typeof value.reason === "string" && value.reason.length <= 80
      ? value.reason
      : value.reason === null
        ? null
        : "malformed_optional_fields";
  const rawRows = value.rows;
  const rows =
    rawRows === undefined && status === "waiting"
      ? []
      : Array.isArray(rawRows) && rawRows.length <= 10
        ? rawRows.map(readSessionHistoryLap)
        : null;
  if (!rows || rows.some((row) => row === null)) return null;
  const populatedRowCount = boundedCount(value.populated_row_count, 100);
  const omittedRowCount = boundedCount(value.omitted_row_count, 90);
  const normalizedRows = rows as LiveSessionHistoryLapRecord[];
  const normalizedCount =
    populatedRowCount ?? (status === "waiting" ? 0 : null);
  const normalizedOmitted = omittedRowCount ?? (status === "waiting" ? 0 : null);
  const ageMs =
    typeof value.age_ms === "number" &&
    Number.isFinite(value.age_ms) &&
    value.age_ms >= 0
      ? value.age_ms
      : null;
  const sessionUid =
    typeof value.session_uid === "string" && /^\d{1,20}$/.test(value.session_uid)
      ? value.session_uid
      : null;
  const packetFormat =
    value.packet_format === 2025 || value.packet_format === 2026
      ? value.packet_format
      : null;
  const playerCarIndex =
    Number.isSafeInteger(value.player_car_index) &&
    (value.player_car_index as number) >= 0 &&
    (value.player_car_index as number) <= 23
      ? (value.player_car_index as number)
      : null;
  const frameIdentifier = uint32(value.frame_identifier);
  const sourceFrameIdentifier = uint32(value.source_frame_identifier);
  const sessionTime =
    typeof value.session_time_s === "number" &&
    Number.isFinite(value.session_time_s) &&
    value.session_time_s >= 0
      ? value.session_time_s
      : null;
  if (
    normalizedCount === null ||
    normalizedOmitted === null ||
    normalizedRows.length !== Math.min(normalizedCount, 10) ||
    normalizedOmitted !== Math.max(0, normalizedCount - normalizedRows.length) ||
    normalizedRows.some(
      (row, index) =>
        row.lap_index !== normalizedCount - normalizedRows.length + index ||
        row.lap_number !== row.lap_index + 1 ||
        (index > 0 && normalizedRows[index - 1].lap_index >= row.lap_index),
    ) ||
    ((status === "fresh" || status === "stale") &&
      (ageMs === null ||
        sessionUid === null ||
        packetFormat === null ||
        playerCarIndex === null ||
        frameIdentifier === null ||
        sourceFrameIdentifier === null)) ||
    (status === "fresh" && (sessionTime === null || reason !== null)) ||
    (status === "stale" && reason === null && sessionTime === null) ||
    (status === "stale" &&
      reason !== null &&
      (normalizedRows.length !== 0 || normalizedCount !== 0 || normalizedOmitted !== 0))
  ) {
    return null;
  }
  return {
    status,
    reason,
    age_ms: ageMs,
    observation_count:
      Number.isSafeInteger(value.observation_count) &&
      (value.observation_count as number) >= 0 &&
      (value.observation_count as number) <= 2_147_483_647
        ? (value.observation_count as number)
        : undefined,
    session_uid: sessionUid,
    packet_format: packetFormat,
    player_car_index: playerCarIndex,
    frame_identifier: frameIdentifier,
    source_frame_identifier: sourceFrameIdentifier,
    session_time_s: sessionTime,
    populated_row_count: normalizedCount,
    omitted_row_count: normalizedOmitted,
    rows: normalizedRows,
  };
}

function readSessionHistoryLap(value: unknown): LiveSessionHistoryLapRecord | null {
  if (!isRecord(value)) return null;
  const lapIndex = boundedCount(value.lap_index, 99);
  const lapNumber = boundedCount(value.lap_number, 100);
  const lapTime = boundedCount(value.lap_time_ms, 0xffff_ffff);
  const flags = boundedCount(value.validity_flags, 255);
  const unknownBits = boundedCount(value.unknown_validity_bits, 255);
  if (
    lapIndex === null ||
    lapNumber === null ||
    lapTime === null ||
    flags === null ||
    unknownBits === null ||
    typeof value.lap_time_available !== "boolean" ||
    typeof value.lap_valid !== "boolean" ||
    typeof value.sector1_valid !== "boolean" ||
    typeof value.sector2_valid !== "boolean" ||
    typeof value.sector3_valid !== "boolean" ||
    !(value.lap_time_unavailable_reason === null ||
      typeof value.lap_time_unavailable_reason === "string") ||
    value.lap_time_available !== (lapTime > 0) ||
    value.lap_valid !== Boolean(flags & 0x01) ||
    value.sector1_valid !== Boolean(flags & 0x02) ||
    value.sector2_valid !== Boolean(flags & 0x04) ||
    value.sector3_valid !== Boolean(flags & 0x08) ||
    unknownBits !== (flags & ~0x0f)
  ) {
    return null;
  }
  const row: Record<string, unknown> = {
    lap_index: lapIndex,
    lap_number: lapNumber,
    lap_time_ms: lapTime,
    lap_time_available: value.lap_time_available,
    lap_time_unavailable_reason: value.lap_time_unavailable_reason,
    validity_flags: flags,
    lap_valid: value.lap_valid,
    sector1_valid: value.sector1_valid,
    sector2_valid: value.sector2_valid,
    sector3_valid: value.sector3_valid,
    unknown_validity_bits: unknownBits,
  };
  for (const sector of [1, 2, 3] as const) {
    const ms = boundedNullableCount(value[`sector${sector}_time_ms`], 15_365_535);
    const part = boundedCount(value[`sector${sector}_time_ms_part`], 65_535);
    const minutes = boundedCount(value[`sector${sector}_time_minutes_part`], 255);
    const available = value[`sector${sector}_time_available`];
    const unavailableReason = value[`sector${sector}_time_unavailable_reason`];
    const expectedTotal =
      part === null || minutes === null ? null : minutes * 60_000 + part;
    const expectedAvailable =
      expectedTotal !== null && expectedTotal > 0 && part! < 60_000;
    if (
      part === null ||
      minutes === null ||
      typeof available !== "boolean" ||
      !(unavailableReason === null || typeof unavailableReason === "string") ||
      ms !== (expectedAvailable ? expectedTotal : null) ||
      available !== expectedAvailable ||
      unavailableReason !==
        (available
          ? null
          : part >= 60_000
            ? "millisecond_component_out_of_range"
            : "reported_zero") ||
      (unavailableReason !== null &&
        unavailableReason !== "reported_zero" &&
        unavailableReason !== "millisecond_component_out_of_range")
    ) {
      return null;
    }
    row[`sector${sector}_time_ms`] = ms;
    row[`sector${sector}_time_ms_part`] = part;
    row[`sector${sector}_time_minutes_part`] = minutes;
    row[`sector${sector}_time_available`] = available;
    row[`sector${sector}_time_unavailable_reason`] = unavailableReason;
  }
  if (
    (value.lap_time_unavailable_reason !== null &&
      value.lap_time_unavailable_reason !== "reported_zero") ||
    (lapTime > 0 && value.lap_time_unavailable_reason !== null) ||
    (lapTime === 0 && value.lap_time_unavailable_reason !== "reported_zero")
  ) {
    return null;
  }
  return row as unknown as LiveSessionHistoryLapRecord;
}

export function readLiveTelemetryMonitor(value: unknown): LiveTelemetryRecord | null {
  const monitor = readMonitor(value);
  if (!monitor || !isRecord(value)) return null;
  return {
    ...(monitor as LiveTelemetryRecord),
    source_epoch:
      typeof value.source_epoch === "string" && value.source_epoch.length <= 64
        ? value.source_epoch
        : null,
    session_uid:
      typeof value.session_uid === "string" && /^\d{1,20}$/.test(value.session_uid)
        ? value.session_uid
        : null,
    frame_identifier:
      Number.isSafeInteger(value.frame_identifier) &&
      (value.frame_identifier as number) >= 0 &&
      (value.frame_identifier as number) <= 0xffff_ffff
        ? (value.frame_identifier as number)
        : null,
    packet_format:
      value.packet_format === 2025 || value.packet_format === 2026
        ? value.packet_format
        : null,
    player_car_index:
      Number.isSafeInteger(value.player_car_index) &&
      (value.player_car_index as number) >= 0 &&
      (value.player_car_index as number) <= 23
        ? (value.player_car_index as number)
        : null,
    session_time_s:
      typeof value.session_time_s === "number" &&
      Number.isFinite(value.session_time_s) &&
      value.session_time_s >= 0 &&
      value.session_time_s <= 86_400
        ? value.session_time_s
        : null,
    speed_kph: rangedNumber(value.speed_kph, 0, 500),
    throttle: rangedNumber(value.throttle, 0, 1),
    brake: rangedNumber(value.brake, 0, 1),
  };
}

function percentArray(value: unknown, integer: boolean): readonly (number | null)[] | null {
  if (!Array.isArray(value) || value.length !== 4) return null;
  return value.map((item) => percent(item, integer));
}

function percent(value: unknown, integer: boolean): number | null {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 100 &&
    (!integer || Number.isInteger(value))
    ? value
    : null;
}

function boundedInteger(value: unknown): number | null {
  return Number.isSafeInteger(value) && (value as number) >= 0
    ? (value as number)
    : null;
}

function boundedCount(value: unknown, maximum: number): number | null {
  return Number.isSafeInteger(value) &&
    (value as number) >= 0 &&
    (value as number) <= maximum
    ? (value as number)
    : null;
}

function boundedNullableCount(value: unknown, maximum: number): number | null | undefined {
  return value === null ? null : boundedCount(value, maximum) ?? undefined;
}

function uint32(value: unknown): number | null {
  return boundedCount(value, 0xffff_ffff);
}

function wireInteger(value: unknown): number | null {
  return Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 255
    ? (value as number)
    : null;
}

function signedWireInteger(value: unknown): number | null {
  return Number.isInteger(value) && (value as number) >= -128 && (value as number) <= 127
    ? (value as number)
    : null;
}

function vector3(value: unknown): readonly [number, number, number] | null {
  if (
    !Array.isArray(value) ||
    value.length !== 3 ||
    !value.every((component) => typeof component === "number" && Number.isFinite(component))
  ) {
    return null;
  }
  return [value[0], value[1], value[2]];
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function rangedNumber(value: unknown, min: number, max: number): number | null {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= min &&
    value <= max
    ? value
    : null;
}

function readMonitor(value: unknown):
  | LiveTelemetryRecord
  | LiveCarStatusRecord
  | LiveLapTimingRecord
  | LiveCarDamageRecord
  | LiveCarSetupRecord
  | LiveSessionConditionsRecord
  | LiveMotionRecord
  | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.status !== "string" ||
    !monitorStates.has(value.status) ||
    !(value.reason === null || typeof value.reason === "string") ||
    !(value.age_ms === null ||
      (typeof value.age_ms === "number" && Number.isFinite(value.age_ms)))
  ) {
    return null;
  }
  return value as unknown as
    | LiveTelemetryRecord
    | LiveCarStatusRecord
    | LiveLapTimingRecord
    | LiveCarDamageRecord
    | LiveCarSetupRecord;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
