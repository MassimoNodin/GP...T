"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import type {
  ApiResponse,
  DraftRegionInput,
  DraftTrackModelBuildResult,
  DraftTrackModelDocument,
  LapRecord,
  SessionContext,
} from "@/lib/api";

type RegionFields = {
  identifier: string;
  label: string;
  start: string;
  end: string;
  brakingStart: string;
  brakingEnd: string;
  turnInStart: string;
  turnInEnd: string;
  throttleStart: string;
  throttleEnd: string;
};

const PRACTICE_QUALIFYING_SESSION_TYPES = new Set([
  "practice_1",
  "practice_2",
  "practice_3",
  "short_practice",
  "qualifying_1",
  "qualifying_2",
  "qualifying_3",
  "short_qualifying",
  "one_shot_qualifying",
  "sprint_shootout_1",
  "sprint_shootout_2",
  "sprint_shootout_3",
  "short_sprint_shootout",
  "one_shot_sprint_shootout",
]);

function emptyRegion(index: number): RegionFields {
  return {
    identifier: `window_${index}`,
    label: `Window ${index}`,
    start: "",
    end: "",
    brakingStart: "",
    brakingEnd: "",
    turnInStart: "",
    turnInEnd: "",
    throttleStart: "",
    throttleEnd: "",
  };
}

function nextRegionNumber(regions: RegionFields[]) {
  const identifiers = new Set(regions.map((region) => region.identifier));
  let next = regions.length + 1;
  while (identifiers.has(`window_${next}`)) next += 1;
  return next;
}

function supportedContext(context: SessionContext | null) {
  const sessionType = context?.session_type;
  return (
    sessionType === "time_trial" ||
    (typeof sessionType === "string" &&
      PRACTICE_QUALIFYING_SESSION_TYPES.has(sessionType))
  );
}

function slug(value: string | null | undefined) {
  return (
    (value ?? "track")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "track"
  );
}

function numberField(value: string, label: string) {
  if (!value.trim()) throw new Error(`${label} is required.`);
  const parsed = Number(value);
  if (!Number.isFinite(parsed))
    throw new Error(`${label} must be a finite number.`);
  return parsed;
}

function optionalWindow(start: string, end: string, label: string) {
  if (!start.trim() && !end.trim()) return undefined;
  if (!start.trim() || !end.trim()) {
    throw new Error(`${label} needs both a start and end distance.`);
  }
  return [
    numberField(start, `${label} start`),
    numberField(end, `${label} end`),
  ] as [number, number];
}

function buildRegions(
  fields: RegionFields[],
  trackLength: number,
): DraftRegionInput[] {
  return fields.map((region, index) => {
    const start = numberField(region.start, `Window ${index + 1} start`);
    const end = numberField(region.end, `Window ${index + 1} end`);
    if (start < 0 || start >= end || end > trackLength) {
      throw new Error(
        `Window ${index + 1} must have increasing bounds within 0–${trackLength.toFixed(1)} m.`,
      );
    }
    const payload: DraftRegionInput = {
      identifier: region.identifier.trim(),
      label: region.label.trim(),
      start_distance_m: start,
      end_distance_m: end,
    };
    if (!payload.identifier || !payload.label) {
      throw new Error(
        `Window ${index + 1} needs both an identifier and a name.`,
      );
    }
    const braking = optionalWindow(
      region.brakingStart,
      region.brakingEnd,
      `Window ${index + 1} braking search window`,
    );
    const turnIn = optionalWindow(
      region.turnInStart,
      region.turnInEnd,
      `Window ${index + 1} turn-in search window`,
    );
    const throttle = optionalWindow(
      region.throttleStart,
      region.throttleEnd,
      `Window ${index + 1} throttle-pickup search window`,
    );
    if (braking) payload.braking_search_window_m = braking;
    if (turnIn) payload.turn_in_search_window_m = turnIn;
    if (throttle) payload.throttle_pickup_window_m = throttle;
    return payload;
  });
}

function downloadModel(model: DraftTrackModelDocument) {
  const blob = new Blob([`${JSON.stringify(model, null, 2)}\n`], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${model.model_id}-r${model.revision}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function DraftTrackModelPanel({
  attempt,
  explicitlySelected,
}: {
  attempt: LapRecord | null;
  explicitlySelected: boolean;
}) {
  const context = attempt?.context ?? null;
  const trackName = context?.track_name ?? "Selected track";
  const trackLength = context?.track_length_m;
  const modeSupported = supportedContext(context);
  const [modelId, setModelId] = useState(`draft-${slug(trackName)}-v1`);
  const [revision, setRevision] = useState("1");
  const [layoutId, setLayoutId] = useState("");
  const [regions, setRegions] = useState<RegionFields[]>([emptyRegion(1)]);
  const [result, setResult] = useState<DraftTrackModelBuildResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function updateRegion(index: number, key: keyof RegionFields, value: string) {
    setRegions((current) =>
      current.map((region, position) =>
        position === index ? { ...region, [key]: value } : region,
      ),
    );
    setResult(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setResult(null);
    if (!attempt || !explicitlySelected) {
      setError("Select an attempt from the lap list to anchor this draft.");
      return;
    }
    if (
      !context ||
      !modeSupported ||
      !Number.isFinite(trackLength) ||
      !trackLength
    ) {
      setError("This attempt does not have stable supported track context.");
      return;
    }
    if (!layoutId.trim()) {
      setError("Enter the circuit layout ID you are using.");
      return;
    }
    let parsedRegions: DraftRegionInput[];
    try {
      parsedRegions = buildRegions(regions, trackLength);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Check the region bounds.",
      );
      return;
    }
    setSubmitting(true);
    try {
      const response = await fetch("/api/track-models/draft", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          source_attempt_key: attempt.attempt_key,
          model_id: modelId.trim(),
          revision: Number(revision),
          layout_id: layoutId.trim(),
          regions: parsedRegions,
        }),
      });
      const payload =
        (await response.json()) as ApiResponse<DraftTrackModelBuildResult>;
      if (!response.ok || payload.status !== "ok" || !payload.data) {
        throw new Error(
          (payload.reason ?? "draft_model_unavailable").replaceAll("_", " "),
        );
      }
      setResult(payload.data);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Draft export could not be built.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section
      className="draft-model-panel panel"
      aria-label="Draft distance-region model authoring"
    >
      <header className="draft-model-heading">
        <div>
          <span className="eyebrow">USER-AUTHORED DISTANCE MODEL</span>
          <h2>Mark analysis windows</h2>
        </div>
        <span className="policy-state policy-manual">DRAFT ONLY</span>
      </header>
      <p className="draft-model-intro">
        Define numeric windows against this attempt&apos;s game-distance scale.
        GP...T checks the bounds and exports a draft JSON file; it does not
        verify circuit geometry or install the model.
      </p>

      {!explicitlySelected ? (
        <p className="draft-model-note" role="status">
          Select an attempt from the lap list first. The authoring source is
          never chosen implicitly.
        </p>
      ) : !modeSupported ? (
        <p className="draft-model-note" role="status">
          Draft authoring supports stable Time Trial and Practice/Qualifying
          attempts. Race and unknown modes remain unavailable.
        </p>
      ) : !context || !trackLength ? (
        <p className="draft-model-note" role="status">
          Track context or lap length is unavailable for this attempt.
        </p>
      ) : (
        <form className="draft-model-form" onSubmit={submit}>
          <div className="draft-model-source">
            <strong>{context.track_name}</strong>
            <span>
              {trackLength.toLocaleString()} m · attempt{" "}
              {attempt?.attempt_number}
            </span>
            <span>
              {attempt?.disposition} · game validity{" "}
              {attempt?.game_valid === true
                ? "valid"
                : attempt?.game_valid === false
                  ? "invalid"
                  : "unknown"}
            </span>
          </div>
          <div className="draft-model-identity">
            <label>
              Model ID
              <input
                value={modelId}
                maxLength={128}
                disabled={submitting}
                onChange={(event) => {
                  setModelId(event.target.value);
                  setResult(null);
                }}
                required
              />
            </label>
            <label>
              Revision
              <input
                type="number"
                min="1"
                step="1"
                value={revision}
                disabled={submitting}
                onChange={(event) => {
                  setRevision(event.target.value);
                  setResult(null);
                }}
                required
              />
            </label>
            <label>
              Layout ID <span>(you declare this)</span>
              <input
                value={layoutId}
                maxLength={128}
                disabled={submitting}
                onChange={(event) => {
                  setLayoutId(event.target.value);
                  setResult(null);
                }}
                placeholder="e.g. grand-prix-layout"
                required
              />
            </label>
          </div>

          <div className="draft-model-region-heading">
            <div>
              <strong>Distance windows</strong>
              <small>
                Use 0–{trackLength.toLocaleString()} m from the game lap origin.
              </small>
            </div>
            <button
              type="button"
              className="secondary-button"
              disabled={submitting || regions.length >= 64}
              onClick={() => {
                setResult(null);
                setRegions((current) => [
                  ...current,
                  emptyRegion(nextRegionNumber(current)),
                ]);
              }}
            >
              Add window
            </button>
          </div>

          <div className="draft-model-regions">
            {regions.map((region, index) => (
              <fieldset
                className="draft-model-region"
                key={`${attempt?.attempt_key}-${index}`}
              >
                <legend>Window {index + 1}</legend>
                <div className="draft-model-region-fields">
                  <label>
                    Identifier
                    <input
                      value={region.identifier}
                      maxLength={128}
                      disabled={submitting}
                      onChange={(event) =>
                        updateRegion(index, "identifier", event.target.value)
                      }
                      required
                    />
                  </label>
                  <label>
                    Name
                    <input
                      value={region.label}
                      maxLength={96}
                      disabled={submitting}
                      onChange={(event) =>
                        updateRegion(index, "label", event.target.value)
                      }
                      required
                    />
                  </label>
                  <label>
                    Start (m)
                    <input
                      type="number"
                      min="0"
                      max={trackLength}
                      step="any"
                      value={region.start}
                      disabled={submitting}
                      onChange={(event) =>
                        updateRegion(index, "start", event.target.value)
                      }
                      required
                    />
                  </label>
                  <label>
                    End (m)
                    <input
                      type="number"
                      min="0"
                      max={trackLength}
                      step="any"
                      value={region.end}
                      disabled={submitting}
                      onChange={(event) =>
                        updateRegion(index, "end", event.target.value)
                      }
                      required
                    />
                  </label>
                </div>
                <details>
                  <summary>Optional event search windows</summary>
                  <div className="draft-model-region-fields draft-model-search-fields">
                    <label>
                      Brake start (m)
                      <input
                        type="number"
                        min="0"
                        max={trackLength}
                        step="any"
                        value={region.brakingStart}
                        disabled={submitting}
                        onChange={(event) =>
                          updateRegion(
                            index,
                            "brakingStart",
                            event.target.value,
                          )
                        }
                      />
                    </label>
                    <label>
                      Brake end (m)
                      <input
                        type="number"
                        min="0"
                        max={trackLength}
                        step="any"
                        value={region.brakingEnd}
                        disabled={submitting}
                        onChange={(event) =>
                          updateRegion(index, "brakingEnd", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      Turn-in start (m)
                      <input
                        type="number"
                        min="0"
                        max={trackLength}
                        step="any"
                        value={region.turnInStart}
                        disabled={submitting}
                        onChange={(event) =>
                          updateRegion(index, "turnInStart", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      Turn-in end (m)
                      <input
                        type="number"
                        min="0"
                        max={trackLength}
                        step="any"
                        value={region.turnInEnd}
                        disabled={submitting}
                        onChange={(event) =>
                          updateRegion(index, "turnInEnd", event.target.value)
                        }
                      />
                    </label>
                    <label>
                      Throttle start (m)
                      <input
                        type="number"
                        min="0"
                        max={trackLength}
                        step="any"
                        value={region.throttleStart}
                        disabled={submitting}
                        onChange={(event) =>
                          updateRegion(
                            index,
                            "throttleStart",
                            event.target.value,
                          )
                        }
                      />
                    </label>
                    <label>
                      Throttle end (m)
                      <input
                        type="number"
                        min="0"
                        max={trackLength}
                        step="any"
                        value={region.throttleEnd}
                        disabled={submitting}
                        onChange={(event) =>
                          updateRegion(index, "throttleEnd", event.target.value)
                        }
                      />
                    </label>
                  </div>
                </details>
                {regions.length > 1 ? (
                  <button
                    type="button"
                    className="draft-model-remove"
                    disabled={submitting}
                    onClick={() => {
                      setResult(null);
                      setRegions((current) =>
                        current.filter((_, position) => position !== index),
                      );
                    }}
                  >
                    Remove window
                  </button>
                ) : null}
              </fieldset>
            ))}
          </div>

          <button
            className="primary-button"
            type="submit"
            disabled={submitting}
          >
            {submitting ? "Validating draft…" : "Build draft JSON"}
          </button>
          {error ? (
            <p className="draft-model-error" role="alert">
              {error}
            </p>
          ) : null}
        </form>
      )}

      {result ? (
        <div className="draft-model-result" role="status">
          <div>
            <strong>
              {result.model.model_id} · revision {result.model.revision}
            </strong>
            <span>
              Exported with draft status. Catalog installation was not
              performed.
            </span>
          </div>
          {result.warnings.length ? (
            <ul>
              {result.warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`}>{warning.text}</li>
              ))}
            </ul>
          ) : null}
          <button
            type="button"
            className="secondary-button"
            onClick={() => downloadModel(result.model)}
          >
            Download {result.model.model_id}-r{result.model.revision}.json
          </button>
        </div>
      ) : null}
    </section>
  );
}
