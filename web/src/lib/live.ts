import type {
  LiveCarDamageRecord,
  LiveCarSetupRecord,
  LiveCarStatusRecord,
  LiveLapTimingRecord,
  LiveMotionRecord,
  LiveSessionConditionsRecord,
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
    live_telemetry: readMonitor(
      source === "recording"
        ? progress?.live_telemetry
        : data.live_telemetry,
    ) as LiveTelemetryRecord | null,
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
