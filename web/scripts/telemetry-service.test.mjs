import test from "node:test";
import assert from "node:assert/strict";
import {
  parseTelemetryServiceResponse,
  telemetryReservationLabel,
} from "../src/lib/telemetry-service.mjs";

function response(overrides = {}) {
  return {
    api_version: "v1",
    status: "ok",
    data: {
      observed_at_utc: "2026-10-06T01:23:45Z",
      udp_bind_host: "0.0.0.0",
      udp_port: 20777,
      receive_queue_size: 8192,
      configuration_available: true,
      controller_ready: true,
      operation_reservation: "idle",
      unavailable_reason: null,
      ...overrides,
    },
    reason: null,
  };
}

test("accepts bounded configured service status", () => {
  const parsed = parseTelemetryServiceResponse(response());
  assert.equal(parsed.udp_bind_host, "0.0.0.0");
  assert.equal(parsed.udp_port, 20777);
  assert.equal(parsed.receive_queue_size, 8192);
  assert.equal(parsed.operation_reservation, "idle");
});

test("accepts controller unavailable while retaining valid configuration", () => {
  const parsed = parseTelemetryServiceResponse(response({
    controller_ready: false,
    operation_reservation: "unavailable",
    unavailable_reason: "recording_controller_unavailable",
  }));
  assert.equal(parsed.controller_ready, false);
  assert.equal(parsed.udp_port, 20777);
});

test("accepts unsupported configuration only when values are withheld", () => {
  const parsed = parseTelemetryServiceResponse(response({
    udp_bind_host: null,
    udp_port: null,
    receive_queue_size: null,
    configuration_available: false,
    unavailable_reason: "telemetry_configuration_unsupported",
  }));
  assert.equal(parsed.configuration_available, false);
  assert.equal(parsed.udp_bind_host, null);
});

test("rejects malformed, inconsistent, or out-of-range telemetry status", () => {
  assert.equal(parseTelemetryServiceResponse(null), null);
  assert.equal(parseTelemetryServiceResponse(response({ udp_port: 65536 })), null);
  assert.equal(parseTelemetryServiceResponse(response({ receive_queue_size: 1_000_001 })), null);
  assert.equal(parseTelemetryServiceResponse(response({ udp_bind_host: "h".repeat(257) })), null);
  assert.equal(parseTelemetryServiceResponse(response({ operation_reservation: "unknown" })), null);
  assert.equal(parseTelemetryServiceResponse(response({ unavailable_reason: "arbitrary" })), null);
  assert.equal(parseTelemetryServiceResponse(response({
    controller_ready: false,
    operation_reservation: "idle",
    unavailable_reason: "recording_controller_unavailable",
  })), null);
});

test("labels each exclusive service reservation", () => {
  assert.equal(telemetryReservationLabel("idle"), "Available");
  assert.equal(telemetryReservationLabel("recording"), "Recording owns the service");
  assert.equal(telemetryReservationLabel("import"), "Import owns the service");
  assert.equal(telemetryReservationLabel("replay"), "Replay owns the service");
  assert.equal(telemetryReservationLabel("upload"), "Upload owns the service");
  assert.equal(telemetryReservationLabel("unavailable"), "Service unavailable");
});
