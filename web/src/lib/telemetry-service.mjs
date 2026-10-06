const RESERVATIONS = new Set([
  "idle",
  "recording",
  "import",
  "replay",
  "upload",
  "unavailable",
]);
const UNAVAILABLE_REASONS = new Set([
  "telemetry_configuration_unsupported",
  "recording_controller_unavailable",
]);

function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isTimestamp(value) {
  return (
    typeof value === "string" &&
    value.length <= 40 &&
    value.endsWith("Z") &&
    Number.isFinite(Date.parse(value))
  );
}

export function parseTelemetryServiceResponse(value) {
  if (!isObject(value) || value.api_version !== "v1" || value.status !== "ok") {
    return null;
  }
  const data = value.data;
  if (
    !isObject(data) ||
    !isTimestamp(data.observed_at_utc) ||
    typeof data.configuration_available !== "boolean" ||
    typeof data.controller_ready !== "boolean" ||
    !RESERVATIONS.has(data.operation_reservation)
  ) {
    return null;
  }

  const validConfiguration =
    data.configuration_available &&
    typeof data.udp_bind_host === "string" &&
    data.udp_bind_host.length > 0 &&
    data.udp_bind_host.length <= 256 &&
    Number.isSafeInteger(data.udp_port) &&
    data.udp_port >= 1 &&
    data.udp_port <= 65535 &&
    Number.isSafeInteger(data.receive_queue_size) &&
    data.receive_queue_size >= 1 &&
    data.receive_queue_size <= 1_000_000;
  const unavailableConfiguration =
    !data.configuration_available &&
    data.udp_bind_host === null &&
    data.udp_port === null &&
    data.receive_queue_size === null;
  const validReason =
    data.unavailable_reason === null ||
    (typeof data.unavailable_reason === "string" &&
      UNAVAILABLE_REASONS.has(data.unavailable_reason));

  if (
    (!validConfiguration && !unavailableConfiguration) ||
    !validReason ||
    (data.controller_ready && data.operation_reservation === "unavailable") ||
    (!data.controller_ready && data.operation_reservation !== "unavailable")
  ) {
    return null;
  }
  if (
    (!data.configuration_available &&
      data.unavailable_reason !== "telemetry_configuration_unsupported") ||
    (data.configuration_available &&
      !data.controller_ready &&
      data.unavailable_reason !== "recording_controller_unavailable") ||
    (data.configuration_available &&
      data.controller_ready &&
      data.unavailable_reason !== null)
  ) {
    return null;
  }

  return {
    observed_at_utc: data.observed_at_utc,
    udp_bind_host: validConfiguration ? data.udp_bind_host : null,
    udp_port: validConfiguration ? data.udp_port : null,
    receive_queue_size: validConfiguration ? data.receive_queue_size : null,
    configuration_available: data.configuration_available,
    controller_ready: data.controller_ready,
    operation_reservation: data.operation_reservation,
    unavailable_reason: data.unavailable_reason,
  };
}

export function telemetryReservationLabel(value) {
  switch (value) {
    case "idle":
      return "Available";
    case "recording":
      return "Recording owns the service";
    case "import":
      return "Import owns the service";
    case "replay":
      return "Replay owns the service";
    case "upload":
      return "Upload owns the service";
    default:
      return "Service unavailable";
  }
}
