"use client";

import { useId, useMemo } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { lapAnalysisFixture } from "@/features/review-data/fixtures";
import { ZoomIcon } from "@/features/ui-foundation/icons";
import { Badge, Button, Card, IconButton, Select } from "@/features/ui-foundation/primitives";
import { EmptyState } from "@/features/ui-foundation/states";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import { useAnalysis } from "./analysis-context";
import { choiceOptionText, formatRange, heatOf, quickJumps, regionDelta, type RegionChoice } from "./regions";
import { TrackMapDrawing, type MapRegion } from "./TrackMapDrawing";
import styles from "./LapAnalysis.module.css";
import viewStyles from "./Views.module.css";

// Illustrative Monaco regions of the reference drawing (fixture session only).
const REGIONS: Record<string, { from: number; to: number; band: { from: number; to: number; inner?: number } }> = {
  "Turn 1 – Turn 5": { from: 0, to: 1180, band: { from: 2300, to: 2300 } },
  "Turn 6 – Turn 8": {
    from: lapAnalysisFixture.region.from,
    to: lapAnalysisFixture.region.to,
    band: lapAnalysisFixture.chart.band,
  },
  "Turn 9 – Turn 10": { from: 2980, to: 3060, band: { from: 2980, to: 3060 } },
  "Turn 11 – Turn 19": { from: 3060, to: 3337, band: { from: 3060, to: 3060 } },
};

const QUICK_JUMPS: Record<string, string> = {
  S1: "Turn 1 – Turn 5",
  S2: "Turn 6 – Turn 8",
  S3: "Turn 11 – Turn 19",
  "T1-5": "Turn 1 – Turn 5",
  "T6-10": "Turn 6 – Turn 8",
};

/** Band drawn on the reference chart for the fixture's selected region. */
export function fixtureBand(region: string | null) {
  const info = region ? REGIONS[region] : null;
  return info && info.band.to > info.band.from ? info.band : null;
}

/** Regions on the map, coloured by the time difference over each (neutral without a reference lap). */
export function useMapRegions(): MapRegion[] {
  const { choices, delta, reference } = useAnalysis();
  return useMemo(
    () =>
      choices.map((choice) => {
        const seconds = reference && delta ? regionDelta(choice.range, delta) : null;
        const heat = reference ? heatOf(seconds) : ("neutral" as const);
        const suffix = seconds === null ? "" : ` · ${seconds > 0 ? "+" : seconds < 0 ? "-" : ""}${Math.abs(seconds).toFixed(2)} s`;
        return { choice, heat, label: `${choice.label}${suffix}` };
      }),
    [choices, delta, reference],
  );
}

export function TrackCard() {
  const state = useAnalysis();
  const { model, view, setView, target, reference, measured } = state;
  const compared = target && reference ? `Lap ${target.lap} vs ${reference.lap}` : target ? `Lap ${target.lap}` : "";
  const mapRegions = useMapRegions();
  const path = state.targetTrace?.state === "available" ? state.targetTrace.path : null;
  return (
    <Card className={styles.trackCard}>
      <div className={styles.trackHead}>
        <div>
          <h2 className={styles.trackTitle}>{model.track}</h2>
          <div className={styles.trackSub}>{compared}</div>
        </div>
        <IconButton label="Enlarge track map" className={styles.zoom} onClick={() => setView(view === "map" ? "overview" : "map")}>
          <ZoomIcon size={15} />
        </IconButton>
      </div>
      {measured ? (
        <TrackMapDrawing
          trackKey={model.trackKey}
          path={path}
          lapLengthM={model.trackLengthM ?? state.regionModel?.trackLengthM ?? null}
          regions={mapRegions}
          selectedId={state.selectedChoice?.id ?? null}
          title={`${model.track} circuit map`}
          className={viewStyles.cardMap}
          strokeWidth={4}
        />
      ) : (
        <TrackMap track={model.trackKey} className={styles.trackMap} strokeWidth={2} title={`${model.track} circuit map`} />
      )}
      {measured ? <RegionPanel /> : <FixtureRegionPanel />}
    </Card>
  );
}

/** Region picker of the reference drawing (fixture session). */
function FixtureRegionPanel() {
  const { fixtureRegion: region, setFixtureRegion: setRegion } = useAnalysis();
  const regionInfo = region ? REGIONS[region] : null;
  return (
    <div className={styles.trackSection}>
      <label className={styles.fieldLabel} htmlFor="region">
        Selected Region
      </label>
      <Select id="region" value={region ?? ""} onChange={(event) => setRegion(event.target.value || null)} className={styles.fullSelect}>
        <option value="">Full lap</option>
        {Object.keys(REGIONS).map((name) => (
          <option key={name}>{name}</option>
        ))}
      </Select>
      <div className={styles.fieldLabel}>Distance</div>
      <div className={styles.fieldValue}>
        {regionInfo ? `${regionInfo.from.toLocaleString("en-US")} m – ${regionInfo.to.toLocaleString("en-US")} m` : "Full lap"}
      </div>
      <div className={styles.regionButtons}>
        <Button onClick={() => setRegion(null)}>Full Lap</Button>
        <Button variant="muted" onClick={() => setRegion(null)}>
          Clear
        </Button>
      </div>
      <div className={styles.fieldLabel}>Quick Jumps</div>
      <div className={styles.jumps}>
        {Object.entries(QUICK_JUMPS).map(([label, target]) => (
          <button key={label} type="button" className={styles.jump} onClick={() => setRegion(target)}>
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

function optionGroups(choices: RegionChoice[]) {
  return { complexes: choices.filter((choice) => choice.complex), singles: choices.filter((choice) => !choice.complex) };
}

/** Region picker for real sessions: track-model regions, or why there are none. */
export function RegionPanel() {
  const state = useAnalysis();
  const { regions, regionModel, choices, selection, selectedChoice, setSelection, brush, setBrush, axis, setView } = state;
  const selectId = useId();
  const full = axis ? `Full lap · 0 m – ${Math.round(axis.to).toLocaleString("en-US")} m` : "Full lap";
  const distance = brush
    ? `Selected range · ${formatRange(brush)}`
    : selectedChoice
      ? formatRange(selectedChoice.range)
      : full;

  if (regions.state === "deferred") {
    return (
      <div className={styles.trackSection} aria-busy="true">
        <div className={styles.fieldLabel}>Selected Region</div>
        <Bone height={34} />
        <p className={viewStyles.regionNote}>Reading the track model…</p>
      </div>
    );
  }

  const none = regions.state !== "available" || !regionModel;
  const { complexes, singles } = optionGroups(choices);
  const jumps = quickJumps(choices);
  return (
    <div className={styles.trackSection}>
      {none ? (
        <>
          <EmptyState
            size="inline"
            title="No corner regions for this circuit."
            body={regions.state === "none" ? regions.reason : undefined}
            action={
              <button type="button" className={viewStyles.textButton} onClick={() => setView("telemetry")}>
                Select a range on the chart
              </button>
            }
          />
          <div className={styles.fieldLabel}>Distance</div>
          <div className={styles.fieldValue}>{distance}</div>
        </>
      ) : (
        <>
          <div className={viewStyles.regionHead}>
            <label className={styles.fieldLabel} htmlFor={selectId}>
              Selected Region
            </label>
            {regionModel.validation === "draft" && <Badge tone="amber">Draft model</Badge>}
          </div>
          <Select
            id={selectId}
            value={selection ?? ""}
            onChange={(event) => setSelection(event.target.value || null)}
            className={styles.fullSelect}
          >
            <option value="">Full lap</option>
            {complexes.length > 0 && (
              <optgroup label="Complexes">
                {complexes.map((choice) => (
                  <option key={choice.id} value={choice.id}>
                    {choiceOptionText(choice)}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label="Regions">
              {singles.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choiceOptionText(choice)}
                </option>
              ))}
            </optgroup>
          </Select>
          <div className={styles.fieldLabel}>Distance</div>
          <div className={styles.fieldValue}>{distance}</div>
          <div className={styles.regionButtons}>
            <Button
              onClick={() => {
                setSelection(null);
              }}
            >
              Full Lap
            </Button>
            <Button
              variant="muted"
              onClick={() => {
                setSelection(null);
                setBrush(null);
              }}
            >
              Clear
            </Button>
          </div>
          {jumps.length > 0 && (
            <>
              <div className={styles.fieldLabel}>Quick Jumps</div>
              <div className={styles.jumps}>
                {jumps.map((jump) => (
                  <button key={jump.id} type="button" className={styles.jump} aria-pressed={selection === jump.id} onClick={() => setSelection(jump.id)}>
                    {jump.label}
                  </button>
                ))}
              </div>
            </>
          )}
          {regionModel.validation === "draft" && <p className={viewStyles.regionNote}>Draft regions are approximate.</p>}
        </>
      )}
    </div>
  );
}
