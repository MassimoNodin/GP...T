export const LIVE_CHART_MAX_POINTS = 120;
export const LIVE_CHART_WINDOW_SECONDS = 60;
const FRESHNESS_LIMIT_MS = 500;
const FRAME_MASK = 0xffff_ffff;
const SERIAL_HALF_RANGE = 0x8000_0000;

export const EMPTY_BROWSER_TELEMETRY_HISTORY = {
  points: [],
  epoch: null,
  sessionUid: null,
  packetFormat: null,
  playerCarIndex: null,
  watermarkFrameIdentifier: null,
  watermarkSessionTimeS: null,
  awaitingMonotonicResume: false,
};

/**
 * Add only a fresh, new source frame to browser-local chart history.
 * Points remain separate because polling does not establish continuous coverage.
 */
export function collectBrowserTelemetryPoint(previous, monitor, sourceState) {
  const point = readPoint(monitor);
  if (!point) return previous;

  const sameIdentity =
    previous.epoch === point.epoch &&
    previous.sessionUid === point.sessionUid &&
    previous.packetFormat === point.packetFormat &&
    previous.playerCarIndex === point.playerCarIndex;
  let history = sameIdentity
    ? previous
    : {
        ...EMPTY_BROWSER_TELEMETRY_HISTORY,
        epoch: point.epoch,
        sessionUid: point.sessionUid,
        packetFormat: point.packetFormat,
        playerCarIndex: point.playerCarIndex,
      };

  if (history.watermarkFrameIdentifier !== null) {
    const distance =
      (point.frameIdentifier - history.watermarkFrameIdentifier) >>> 0;
    if (distance === 0 || distance >= SERIAL_HALF_RANGE) return previous;
    if (
      history.watermarkSessionTimeS !== null &&
      point.sessionTimeS < history.watermarkSessionTimeS
    ) {
      return {
        ...history,
        points: [],
        watermarkFrameIdentifier: point.frameIdentifier,
        watermarkSessionTimeS: point.sessionTimeS,
        awaitingMonotonicResume: true,
      };
    }
  }

  history = {
    ...history,
    watermarkFrameIdentifier: point.frameIdentifier,
    watermarkSessionTimeS: point.sessionTimeS,
  };

  if (sourceState === "paused") return history;
  if (history.awaitingMonotonicResume) {
    history = { ...history, awaitingMonotonicResume: false };
  }

  const latest = history.points.at(-1);
  if (
    latest &&
    (latest.epoch !== point.epoch ||
      latest.sessionUid !== point.sessionUid ||
      latest.packetFormat !== point.packetFormat ||
      latest.playerCarIndex !== point.playerCarIndex)
  ) {
    history = { ...history, points: [] };
  }

  const retained = history.points.filter(
    (item) =>
      point.sessionTimeS - item.sessionTimeS <= LIVE_CHART_WINDOW_SECONDS,
  );
  return {
    ...history,
    points: [...retained, point].slice(-LIVE_CHART_MAX_POINTS),
  };
}

function readPoint(monitor) {
  const epoch = monitor.source_epoch;
  const sessionUid = monitor.session_uid;
  const frameIdentifier = monitor.frame_identifier;
  const packetFormat = monitor.packet_format;
  const playerCarIndex = monitor.player_car_index;
  const sessionTimeS = monitor.session_time_s;
  if (
    monitor.status !== "fresh" ||
    monitor.age_ms === null ||
    !Number.isFinite(monitor.age_ms) ||
    monitor.age_ms < 0 ||
    monitor.age_ms > FRESHNESS_LIMIT_MS ||
    typeof epoch !== "string" ||
    epoch.length < 1 ||
    epoch.length > 64 ||
    typeof sessionUid !== "string" ||
    !/^[0-9]{1,20}(?![\s\S])/.test(sessionUid) ||
    !Number.isInteger(frameIdentifier) ||
    frameIdentifier < 0 ||
    frameIdentifier > FRAME_MASK ||
    (packetFormat !== 2025 && packetFormat !== 2026) ||
    !Number.isInteger(playerCarIndex) ||
    playerCarIndex < 0 ||
    playerCarIndex > 23 ||
    typeof sessionTimeS !== "number" ||
    !Number.isFinite(sessionTimeS) ||
    sessionTimeS < 0 ||
    sessionTimeS > 86_400
  ) {
    return null;
  }

  const speedKph = bounded(monitor.speed_kph, 0, 500);
  const throttle = bounded(monitor.throttle, 0, 1);
  const brake = bounded(monitor.brake, 0, 1);
  const gear = Number.isInteger(monitor.gear)
    ? bounded(monitor.gear, -1, 8)
    : null;
  if (speedKph === null && throttle === null && brake === null && gear === null)
    return null;

  return {
    epoch,
    sessionUid,
    packetFormat,
    playerCarIndex,
    frameIdentifier,
    sessionTimeS,
    speedKph,
    throttle,
    brake,
    gear,
  };
}

function bounded(value, min, max) {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= min &&
    value <= max
    ? value
    : null;
}
