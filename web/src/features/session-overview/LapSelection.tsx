"use client";

import Link from "next/link";
import { formatDelta, formatLapTime } from "@/features/review-data/format";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  OpenDocumentIcon,
  StatusGlyph,
  SwapIcon,
} from "@/features/ui-foundation/icons";
import { LapStepper } from "@/features/ui-foundation/LapStepper";
import { Card, IconButton } from "@/features/ui-foundation/primitives";
import { displayDelta, useUnits } from "@/features/ui-foundation/units";
import { Segmented } from "@/features/ui-foundation/Segmented";
import { compareSentence, compareWarnings, type LapMode, type ResolvedCompare } from "./overview-data";
import type { OverviewLap } from "./types";
import styles from "./SessionOverview.module.css";
import viewStyles from "./OverviewViews.module.css";

const MODES = [
  { key: "single", label: "Single Lap" },
  { key: "compare", label: "Compare Laps" },
] as const;

export function LapSelection({
  laps,
  bestId,
  mode,
  onModeChange,
  selectedId,
  onSelect,
  compare,
  onReference,
  onSwap,
  compareHref,
}: {
  laps: OverviewLap[];
  bestId: string | null;
  mode: LapMode;
  onModeChange: (mode: LapMode) => void;
  selectedId: string;
  onSelect: (id: string) => void;
  /** Resolved target/reference pair (compare mode). */
  compare: ResolvedCompare;
  onReference: (id: string) => void;
  onSwap: () => void;
  /** Lap Analysis link carrying the pair. */
  compareHref: string;
}) {
  const units = useUnits();
  const index = Math.max(0, laps.findIndex((lap) => lap.id === selectedId));
  const selected = laps[index];
  const best = laps.find((lap) => lap.id === bestId) ?? null;
  const canCompare = laps.length >= 2;
  const { target, reference } = compare;
  const warnings = target && reference ? compareWarnings(target, reference) : [];
  const standard = "Comparisons are only available for laps with complete telemetry data.";

  return (
    <Card className={styles.selectionCard}>
      <h2 className={styles.selectionTitle}>Lap Selection</h2>
      <div className={styles.selectionBody}>
        <Segmented
          items={[...MODES]}
          value={mode}
          onChange={onModeChange}
          label="Lap selection mode"
          className={styles.modeTabs}
          disabled={!canCompare}
          disabledReason="Comparing needs at least two timed laps"
        />
        {mode === "single" && selected && (
          <div className={styles.stepper}>
            <IconButton
              label="Previous lap"
              className={styles.stepButton}
              disabled={index === 0}
              onClick={() => onSelect(laps[index - 1].id)}
            >
              <ChevronLeftIcon size={15} />
            </IconButton>
            <div className={styles.stepValue} aria-live="polite">
              <span className={styles.stepLap}>Lap {selected.lap}</span>
              <span className={styles.stepTime}>{formatLapTime(selected.ms)}</span>
              {best && <span className={styles.stepDelta}>{displayDelta(formatDelta(selected.ms - best.ms), units)}</span>}
            </div>
            <IconButton
              label="Next lap"
              className={styles.stepButton}
              disabled={index >= laps.length - 1}
              onClick={() => onSelect(laps[index + 1].id)}
            >
              <ChevronRightIcon size={15} />
            </IconButton>
          </div>
        )}
        {mode === "compare" && (
          <div className={viewStyles.compareRow}>
            <LapStepper
              label="Target"
              tone="target"
              labelDot
              laps={laps}
              value={target?.id ?? null}
              onChange={onSelect}
            />
            <IconButton
              label="Swap target and reference"
              className={viewStyles.swapButton}
              disabled={!target || !reference}
              onClick={onSwap}
            >
              <SwapIcon size={16} />
            </IconButton>
            <LapStepper
              label="Reference"
              tone="reference"
              labelDot
              laps={laps}
              value={reference?.id ?? null}
              onChange={onReference}
              note={reference && reference.id === bestId ? "Session best" : undefined}
            />
            <Link href={compareHref} className={viewStyles.compareLink}>
              <OpenDocumentIcon size={15} />
              Compare in Lap Analysis
            </Link>
          </div>
        )}
        <div className={styles.bestNote}>
          <StatusGlyph tone="blue" glyph="info" size={24} />
          <div>
            {mode === "compare" ? (
              <>
                <div className={styles.bestTitle}>
                  {target && reference ? compareSentence(target, reference) : "Choose two timed laps to compare."}
                </div>
                <div className={styles.bestBody}>
                  {warnings.length > 0
                    ? warnings.map((warning) => (
                        <span key={warning} className={viewStyles.bestWarning}>
                          {warning}
                        </span>
                      ))
                    : standard}
                </div>
              </>
            ) : (
              <>
                <div className={styles.bestTitle}>
                  {best ? `Session best: ${formatLapTime(best.ms)} (Lap ${best.lap})` : "No eligible session best lap"}
                </div>
                <div className={styles.bestBody}>{standard}</div>
              </>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
