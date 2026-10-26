import assert from "node:assert/strict";
import test from "node:test";
import {
  collectBrowserTelemetryPoint,
  EMPTY_BROWSER_TELEMETRY_HISTORY,
} from "../src/lib/live-chart.mjs";

function sample(overrides = {}) {
  return {
    status: "fresh",
    reason: null,
    age_ms: 0,
    source_epoch: "epoch-a",
    session_uid: "123456789",
    frame_identifier: 10,
    packet_format: 2025,
    player_car_index: 0,
    session_time_s: 10,
    speed_kph: 200,
    gear: 7,
    throttle: 0.7,
    brake: 0,
    ...overrides,
  };
}

test("deduplicates repeated and stale snapshots, including paused replay polls", () => {
  const first = collectBrowserTelemetryPoint(
    EMPTY_BROWSER_TELEMETRY_HISTORY,
    sample(),
    "playing",
  );
  assert.equal(first.points.length, 1);
  assert.equal(first.points[0].gear, 7);
  assert.equal(collectBrowserTelemetryPoint(first, sample(), "playing"), first);
  assert.equal(
    collectBrowserTelemetryPoint(
      first,
      sample({ status: "stale", frame_identifier: 11 }),
      "playing",
    ),
    first,
  );

  const paused = collectBrowserTelemetryPoint(
    EMPTY_BROWSER_TELEMETRY_HISTORY,
    sample(),
    "paused",
  );
  assert.equal(paused.points.length, 0);
  assert.equal(collectBrowserTelemetryPoint(paused, sample(), "playing"), paused);
  const resumed = collectBrowserTelemetryPoint(
    paused,
    sample({ frame_identifier: 11, session_time_s: 10.5 }),
    "playing",
  );
  assert.equal(resumed.points.length, 1);
});

test("accepts frame wrap and clears when an epoch or identity changes", () => {
  const beforeWrap = collectBrowserTelemetryPoint(
    EMPTY_BROWSER_TELEMETRY_HISTORY,
    sample({ frame_identifier: 0xffff_fff0 }),
    "playing",
  );
  const afterWrap = collectBrowserTelemetryPoint(
    beforeWrap,
    sample({ frame_identifier: 2, session_time_s: 10.5 }),
    "playing",
  );
  assert.equal(afterWrap.points.length, 2);

  const newEpoch = collectBrowserTelemetryPoint(
    afterWrap,
    sample({ source_epoch: "epoch-b", frame_identifier: 3 }),
    "playing",
  );
  assert.equal(newEpoch.points.length, 1);
  assert.equal(newEpoch.points[0].epoch, "epoch-b");

  const newPlayer = collectBrowserTelemetryPoint(
    newEpoch,
    sample({ source_epoch: "epoch-b", player_car_index: 1, frame_identifier: 4 }),
    "playing",
  );
  assert.equal(newPlayer.points.length, 1);
  assert.equal(newPlayer.points[0].playerCarIndex, 1);
});

test("validates channels independently and preserves a regression watermark", () => {
  const first = collectBrowserTelemetryPoint(
    EMPTY_BROWSER_TELEMETRY_HISTORY,
    sample(),
    "playing",
  );
  const next = collectBrowserTelemetryPoint(
    first,
    sample({
      frame_identifier: 11,
      session_time_s: 10.5,
      speed_kph: 501,
      throttle: 0.5,
      brake: -0.1,
    }),
    "playing",
  );
  assert.equal(next.points[1].speedKph, null);
  assert.equal(next.points[1].throttle, 0.5);
  assert.equal(next.points[1].brake, null);

  const regressed = collectBrowserTelemetryPoint(
    next,
    sample({ frame_identifier: 12, session_time_s: 9 }),
    "playing",
  );
  assert.equal(regressed.points.length, 0);
  assert.equal(regressed.awaitingMonotonicResume, true);
  assert.equal(
    collectBrowserTelemetryPoint(
      regressed,
      sample({ frame_identifier: 12, session_time_s: 9 }),
      "playing",
    ),
    regressed,
  );
  const resumed = collectBrowserTelemetryPoint(
    regressed,
    sample({ frame_identifier: 13, session_time_s: 9.1 }),
    "playing",
  );
  assert.equal(resumed.points.length, 1);
  assert.equal(resumed.awaitingMonotonicResume, false);
});

test("keeps no more than 120 points or 60 source seconds", () => {
  let history = EMPTY_BROWSER_TELEMETRY_HISTORY;
  for (let frame = 0; frame < 180; frame += 1) {
    history = collectBrowserTelemetryPoint(
      history,
      sample({
        frame_identifier: frame,
        session_time_s: frame * 0.25,
      }),
      "playing",
    );
  }
  assert.equal(history.points.length, 120);

  history = EMPTY_BROWSER_TELEMETRY_HISTORY;
  for (let frame = 0; frame < 40; frame += 1) {
    history = collectBrowserTelemetryPoint(
      history,
      sample({ frame_identifier: frame, session_time_s: frame * 2 }),
      "playing",
    );
  }
  assert.ok(history.points.length <= 31);
  assert.ok(
    history.points.at(-1).sessionTimeS - history.points[0].sessionTimeS <= 60,
  );
});

test("rejects invalid continuity metadata and out-of-range age", () => {
  assert.deepEqual(
    collectBrowserTelemetryPoint(
      EMPTY_BROWSER_TELEMETRY_HISTORY,
      sample({ source_epoch: null }),
      "playing",
    ),
    EMPTY_BROWSER_TELEMETRY_HISTORY,
  );
  assert.deepEqual(
    collectBrowserTelemetryPoint(
      EMPTY_BROWSER_TELEMETRY_HISTORY,
      sample({ age_ms: 501 }),
      "playing",
    ),
    EMPTY_BROWSER_TELEMETRY_HISTORY,
  );
});
