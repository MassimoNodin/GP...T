import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

async function loadTypeScript(url) {
  const source = readFileSync(new URL(url, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(
    `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
  );
}

const live = await loadTypeScript("../src/lib/live.ts");
const hudDisplay = await loadTypeScript("../src/lib/hud-display.ts");

function makeSnapshot({
  telemetryStatus = "fresh",
  timingStatus = "fresh",
  telemetry = {},
  timing = {},
} = {}) {
  return {
    source: "replay",
    operation_id: "a".repeat(32),
    state: "playing",
    context: null,
    capture_name: "sample.f1ecap",
    speed: 1,
    live_telemetry: {
      status: telemetryStatus,
      reason: null,
      age_ms: 120,
      source_epoch: "epoch-1234",
      session_uid: "42",
      packet_format: 2025,
      player_car_index: 0,
      speed_kph: 238.4,
      gear: 7,
      engine_rpm: 12_000,
      throttle: 0.65,
      brake: 0.1,
      ...telemetry,
    },
    live_lap_timing: {
      status: timingStatus,
      reason: null,
      age_ms: 340,
      session_uid: "42",
      packet_format: 2025,
      player_car_index: 0,
      lap_number: 2,
      current_lap_time_ms: 89_567,
      ...timing,
    },
  };
}

test("pinned selection rejects blocked, repeated, malformed, and partial links", () => {
  const operationId = "a".repeat(32);
  assert.deepEqual(live.readPinnedLiveSelection({}, false), {
    status: "unselected",
    source: null,
    operationId: null,
  });
  assert.equal(
    live.readPinnedLiveSelection(
      { live_source: "replay", live_operation_id: operationId },
      false,
    ).status,
    "ready",
  );
  assert.equal(
    live.readPinnedLiveSelection(
      {
        live_source: ["replay", "recording"],
        live_operation_id: operationId,
      },
      false,
    ).status,
    "invalid",
  );
  assert.equal(
    live.readPinnedLiveSelection(
      { live_source: "latest", live_operation_id: operationId },
      false,
    ).status,
    "invalid",
  );
  assert.equal(
    live.readPinnedLiveSelection({ live_source: "replay" }, false).status,
    "invalid",
  );
  assert.equal(
    live.readPinnedLiveSelection(
      { live_source: "replay", live_operation_id: operationId },
      true,
    ).status,
    "invalid",
  );
});

test("HUD values require fresh groups and valid numeric fields", () => {
  const display = hudDisplay.projectHudSnapshot(makeSnapshot());
  assert.deepEqual(
    display.telemetry.fields.map((field) => field.value),
    ["238 km/h", "7", "12,000", "65%", "10%"],
  );
  assert.deepEqual(
    display.lapTiming.fields.map((field) => field.value),
    ["2", "1:29.567"],
  );
  assert.equal(display.telemetry.source, "EPOCH epoch-12");

  const stale = hudDisplay.projectHudSnapshot(
    makeSnapshot({ telemetryStatus: "stale", timingStatus: "stale" }),
  );
  assert.ok(stale.telemetry.fields.every((field) => field.value === "—"));
  assert.ok(stale.lapTiming.fields.every((field) => field.value === "—"));
  assert.equal(stale.telemetry.age, "AGE 0.1 s");
});

test("HUD hides timing across mismatched identity and rejects malformed values", () => {
  const display = hudDisplay.projectHudSnapshot(
    makeSnapshot({
      telemetry: { speed_kph: 501, throttle: -0.1 },
      timing: { player_car_index: 1 },
    }),
  );
  assert.equal(display.telemetry.fields[0].value, "—");
  assert.equal(display.telemetry.fields[3].value, "—");
  assert.equal(display.telemetry.fields[1].value, "7");
  assert.equal(display.lapTiming.status, "identity mismatch");
  assert.ok(display.lapTiming.fields.every((field) => field.value === "—"));
});

test("HUD validates each group's identity independently", () => {
  const noTelemetryIdentity = hudDisplay.projectHudSnapshot(
    makeSnapshot({ telemetry: { session_uid: null } }),
  );
  assert.ok(
    noTelemetryIdentity.telemetry.fields.every((field) => field.value === "—"),
  );
  assert.equal(noTelemetryIdentity.telemetry.status, "identity unavailable");
  assert.equal(noTelemetryIdentity.lapTiming.status, "fresh");
  assert.deepEqual(
    noTelemetryIdentity.lapTiming.fields.map((field) => field.value),
    ["2", "1:29.567"],
  );

  const malformed = hudDisplay.projectHudSnapshot(
    makeSnapshot({
      telemetry: {
        session_uid: { value: "42" },
        source_epoch: { value: "epoch" },
      },
      timing: {
        session_uid: { value: "42" },
        player_car_index: "0",
        packet_format: { value: 2025 },
      },
    }),
  );
  assert.equal(malformed.lapTiming.status, "identity unavailable");
  assert.equal(malformed.lapTiming.source, "PLAYER ? · SESSION ? · FORMAT ?");
  assert.equal(malformed.telemetry.status, "identity unavailable");
  assert.ok(malformed.telemetry.fields.every((field) => field.value === "—"));
  assert.ok(malformed.lapTiming.fields.every((field) => field.value === "—"));
});

test("fresh Lap Data remains available while Car Telemetry is waiting", () => {
  const display = hudDisplay.projectHudSnapshot(
    makeSnapshot({
      telemetryStatus: "waiting",
      telemetry: {
        session_uid: null,
        packet_format: null,
        player_car_index: null,
        source_epoch: null,
      },
    }),
  );
  assert.ok(display.telemetry.fields.every((field) => field.value === "—"));
  assert.equal(display.lapTiming.status, "fresh");
  assert.deepEqual(
    display.lapTiming.fields.map((field) => field.value),
    ["2", "1:29.567"],
  );

  const noTelemetry = hudDisplay.projectHudSnapshot({
    ...makeSnapshot(),
    live_telemetry: null,
  });
  assert.equal(noTelemetry.lapTiming.status, "fresh");
  assert.deepEqual(
    noTelemetry.lapTiming.fields.map((field) => field.value),
    ["2", "1:29.567"],
  );
});

test("HUD follows the game lap clock's positive uint32 bounds", () => {
  const sentinels = hudDisplay.projectHudSnapshot(
    makeSnapshot({ timing: { lap_number: 0, current_lap_time_ms: 0 } }),
  );
  assert.deepEqual(
    sentinels.lapTiming.fields.map((field) => field.value),
    ["—", "—"],
  );

  const longClock = hudDisplay.projectHudSnapshot(
    makeSnapshot({ timing: { current_lap_time_ms: 86_400_001 } }),
  );
  assert.equal(longClock.lapTiming.fields[1].value, "1440:00.001");

  const maxClock = hudDisplay.projectHudSnapshot(
    makeSnapshot({ timing: { current_lap_time_ms: 0xffff_ffff } }),
  );
  assert.equal(maxClock.lapTiming.fields[1].value, "71582:47.295");
});

test("paused replay remains active so delivery ages keep advancing", () => {
  assert.equal(
    live.isPinnedLiveActive({ source: "replay", state: "paused" }),
    true,
  );
  assert.equal(
    live.isPinnedLiveActive({ source: "recording", state: "complete" }),
    false,
  );
});
