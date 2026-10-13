"use client";

import { useMemo } from "react";
import { formatDelta, formatLapTime } from "@/features/review-data/format";
import { SwapIcon } from "@/features/ui-foundation/icons";
import { IconButton, PageHeader, Tabs } from "@/features/ui-foundation/primitives";
import { displayDelta, useUnits } from "@/features/ui-foundation/units";
import { LapStepper } from "@/features/ui-foundation/LapStepper";
import { Segmented } from "@/features/ui-foundation/Segmented";
import { AnalysisProvider, useAnalysis } from "./analysis-context";
import { EngineerQuestions } from "./EngineerQuestions";
import type { LapAnalysisModel } from "./load";
import { VIEWS, type CompareMode, type InitialState } from "./url-state";
import { InputsView } from "./views/InputsView";
import { LegacyView } from "./views/LegacyView";
import { OverviewView } from "./views/OverviewView";
import { QualityView } from "./views/QualityView";
import { SectorsView } from "./views/SectorsView";
import { TelemetryView } from "./views/TelemetryView";
import { TrackMapView } from "./views/TrackMapView";
import styles from "./LapAnalysis.module.css";

const PANEL_ID = "analysis-panel";
const COMPARE_ITEMS: Array<{ key: CompareMode; label: string }> = [
  { key: "delta", label: "Time Delta" },
  { key: "distance", label: "Distance" },
];

export function LapAnalysis({ model, initial }: { model: LapAnalysisModel; initial: InitialState }) {
  return (
    <AnalysisProvider model={model} initial={initial}>
      <Body />
    </AnalysisProvider>
  );
}

function Body() {
  const units = useUnits();
  const state = useAnalysis();
  const { model, laps, view, setView, compare, setCompare, reference, targetIndex, measured, lapDelta } = state;

  const stepperLaps = useMemo(
    () => laps.map((lap) => ({ id: lap.id, lap: lap.lap, ms: lap.ms, valid: lap.valid !== false })),
    [laps],
  );

  return (
    <div className={styles.layout}>
      <div className={styles.main}>
        <div className={styles.headerRow}>
          <PageHeader
            title="Lap Analysis"
            subtitle="Analyse a single lap or compare two laps with telemetry, track map and AI insights."
          />
          <div className={styles.pickers}>
            <LapStepper
              label="Target Lap"
              tone="target"
              laps={stepperLaps}
              value={laps[targetIndex]?.id ?? null}
              onChange={state.setTargetId}
              formatTime={formatLapTime}
              trailing={lapDelta !== null ? <span className={styles.pickerDelta}>{displayDelta(formatDelta(lapDelta), units)}</span> : null}
            />
            {measured && (
              <IconButton
                label="Swap target and reference"
                disabled={!reference}
                title={reference ? "Swap target and reference" : "Select a reference lap to swap"}
                onClick={state.swap}
              >
                <SwapIcon size={15} />
              </IconButton>
            )}
            <LapStepper
              label="Reference Lap"
              tone="reference"
              labelDot="var(--green)"
              laps={stepperLaps}
              value={reference?.id ?? null}
              onChange={state.setReferenceId}
              formatTime={formatLapTime}
              note={reference && reference.id === model.bestId ? "(Session Best)" : null}
            />
          </div>
        </div>

        <div className={styles.tabRow}>
          <Tabs
            items={VIEWS}
            value={view}
            onChange={setView}
            label="Analysis views"
            size="sm"
            idPrefix="analysis-tab"
            controls={() => PANEL_ID}
          />
          <div className={styles.compare}>
            <span>Compare</span>
            <Segmented
              items={COMPARE_ITEMS}
              value={compare}
              onChange={setCompare}
              label="Comparison axis"
              size="sm"
              className={styles.compareTabs}
              disabled={measured && !reference}
              disabledReason="Select a reference lap to compare"
            />
          </div>
        </div>

        <div
          className={styles.content}
          data-view={view}
          data-real={measured ? "true" : undefined}
          id={PANEL_ID}
          role="tabpanel"
          aria-labelledby={`analysis-tab-${view}`}
          tabIndex={0}
        >
          {!measured ? <LegacyView view={view} /> : <RealView />}
        </div>
      </div>

      <EngineerQuestions />
    </div>
  );
}

function RealView() {
  const { view } = useAnalysis();
  switch (view) {
    case "telemetry":
      return <TelemetryView />;
    case "sectors":
      return <SectorsView />;
    case "map":
      return <TrackMapView />;
    case "inputs":
      return <InputsView />;
    case "quality":
      return <QualityView />;
    default:
      return <OverviewView />;
  }
}
