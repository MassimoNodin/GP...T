"use client";

import { useMemo, useState } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { ZoomIcon } from "@/features/ui-foundation/icons";
import { Badge, Card, IconButton } from "@/features/ui-foundation/primitives";
import { EmptyState } from "@/features/ui-foundation/states";
import { useAnalysis } from "../analysis-context";
import { formatRange, heatOf, regionDelta, type Heat, type RegionChoice } from "../regions";
import { useMapRegions } from "../TrackCard";
import { TrackMapDrawing, type MapRegion } from "../TrackMapDrawing";
import { Swatch } from "./parts";
import styles from "../Views.module.css";

const BINS = 8;

function signedSeconds(seconds: number | null): string {
  if (seconds === null) return "No data";
  const sign = seconds > 0 ? "+" : seconds < 0 ? "-" : "";
  return `${sign}${Math.abs(seconds).toFixed(2)} s`;
}

const HEAT_LABEL: Record<Heat | "neutral", string> = {
  gain: "Gained",
  loss: "Lost",
  even: "About equal",
  none: "No data",
  neutral: "",
};

/** Large circuit map with the regions coloured by time difference, and the accessible region list. */
export function TrackMapView() {
  const state = useAnalysis();
  const { model, target, reference, regions, regionModel, selectedChoice, setSelection, delta, axis, targetTrace } = state;
  const [focused, setFocused] = useState(false);
  const mapRegions = useMapRegions();
  const path = targetTrace?.state === "available" ? targetTrace.path : null;
  const lapLength = model.trackLengthM ?? regionModel?.trackLengthM ?? axis?.to ?? null;

  // Without a track model the whole lap is split into equal stretches coloured by the running delta.
  const bins = useMemo<MapRegion[]>(() => {
    if (regions.state === "available" || !lapLength || !reference || !delta) return [];
    return Array.from({ length: BINS }, (_, index): MapRegion => {
      const range = { from: (lapLength / BINS) * index, to: (lapLength / BINS) * (index + 1) };
      const choice: RegionChoice = { id: `bin:${index}`, label: `Part ${index + 1}`, range, apexM: null, complex: false, members: [], complexId: null };
      const seconds = regionDelta(range, delta);
      return { choice, heat: heatOf(seconds), label: `${choice.label} · ${signedSeconds(seconds)}` };
    });
  }, [delta, lapLength, reference, regions.state]);

  const drawn = regions.state === "available" ? mapRegions : bins;
  const list = mapRegions;
  const compared = target && reference ? `Lap ${target.lap} vs ${reference.lap}` : target ? `Lap ${target.lap}` : "";
  const canFocus = !!path && !!selectedChoice;

  return (
    <div className={styles.mapLayout}>
      <Card className={styles.mapCard}>
        <div className={styles.mapHead}>
          <div>
            <h2 className={styles.mapTitle}>{model.track}</h2>
            <div className={styles.mapSub}>{compared}</div>
          </div>
          <IconButton
            label="Fit selected region"
            aria-pressed={focused && canFocus}
            disabled={!canFocus}
            title={canFocus ? "Fit selected region" : "Select a region on a measured line to zoom"}
            onClick={() => setFocused((current) => !current)}
          >
            <ZoomIcon size={15} />
          </IconButton>
        </div>
        <TrackMapDrawing
          trackKey={model.trackKey}
          path={path}
          lapLengthM={lapLength}
          regions={drawn}
          selectedId={selectedChoice?.id ?? null}
          onSelect={regions.state === "available" ? setSelection : undefined}
          title={`${model.track} circuit map${compared ? `, ${compared}` : ""}`}
          className={styles.bigMap}
          strokeWidth={4}
          focusId={focused && canFocus ? (selectedChoice?.id ?? null) : null}
          // No chart here, so only a cursor pinned on another tab is shown.
          cursor={state.pinned ? state.cursor : null}
        />
        <p className={styles.mapNote}>
          {path
            ? `Line drawn from Lap ${target?.lap ?? ""}'s recorded positions.`
            : "Schematic outline; region positions are approximate."}
        </p>
      </Card>
      <Card title="Regions" labelledBy="map-regions" className={styles.regionsCard}>
        {reference && delta ? (
          <ul className={styles.legend} aria-label="Legend">
            <li>
              <Swatch color="var(--gain)" /> Gained
            </li>
            <li>
              <Swatch color="var(--loss)" /> Lost
            </li>
            <li>
              <Swatch color="var(--grey-soft)" /> No data
            </li>
          </ul>
        ) : (
          <p className={styles.mapNote}>Select a reference lap to colour regions by time difference.</p>
        )}
        {regions.state === "deferred" ? (
          <div aria-busy="true">
            <Bone height={14} />
            <Bone height={14} style={{ marginTop: 10 }} />
          </div>
        ) : regions.state !== "available" ? (
          <EmptyState title="No corner regions for this circuit" body={regions.state === "none" ? regions.reason : undefined} />
        ) : (
          <>
            {regionModel?.validation === "draft" && (
              <p className={styles.mapNote}>
                <Badge tone="amber">Draft</Badge> Draft regions are approximate.
              </p>
            )}
            <ul className={styles.regionList}>
              {list.map((item) => (
                <li key={item.choice.id}>
                  <button
                    type="button"
                    className={styles.regionRow}
                    aria-pressed={selectedChoice?.id === item.choice.id}
                    onClick={() => setSelection(selectedChoice?.id === item.choice.id ? null : item.choice.id)}
                  >
                    <span className={styles.regionName}>
                      {item.choice.label}
                      {item.choice.complex && <span className={styles.muted}> (complex)</span>}
                    </span>
                    <span className={styles.muted}>{formatRange(item.choice.range)}</span>
                    <span className={styles.regionDelta} data-heat={item.heat}>
                      {reference && delta ? signedSeconds(regionDelta(item.choice.range, delta)) : ""}
                      {reference && delta && HEAT_LABEL[item.heat] ? <span className="sr-only"> ({HEAT_LABEL[item.heat]})</span> : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
    </div>
  );
}
