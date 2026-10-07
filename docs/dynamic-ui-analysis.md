# Dynamic UI analysis input and integration

## Decision 0089: render track regions and lap traces from explicit evidence

Accepted architecture review by the `gpt-6.1-sol` specialist, 2026-10-07.

The mock images define layout, spacing and visual treatment. Live, Track Analysis and Lap Comparison now render selected API evidence through reusable client components. The existing Python API, exact attempt selection and provenance checks remain the source of recorded analysis. No additional analysis service is required.

Track geometry accepts arbitrary coordinate shapes with distance anchors. Each input segment is independent: gaps stay open and paths are only closed if the supplied points explicitly close them. Maps fit both coordinate axes at equal scale. Recorded trajectories are labelled observed driven paths, without claiming track limits or a centreline.

Region boundaries are measured in lap distance and already include the model distance origin. Supported, connected backend interval deltas classify target minus reference: negative Faster, positive Slower, and ±0.010 seconds Similar. Unknown evidence remains grey. Draft regions are presented as comparisons, without ranking or causal coaching claims. Explicit caller classifications without a numeric delta are labelled supplied classifications.

Comparison traces use the API's shared distance grid, values and masks. Speed is converted from m/s to km/h; throttle/brake remain fractions and display as percentages; steering is normalized input and gear uses steps including reverse. Unsupported values break traces. Rendering is limited to 128 runs and 2,000 displayed points per series; cursor values always read original samples. Large inputs may omit visual detail, as noted beside the chart.

Live telemetry retains pinned-operation polling and freshness rules. Unsupported DRS, ERS, engine mode and live map geometry display unavailable values. Fuel retains its reported unit rather than inventing litres. A complete observed path is available in Track Analysis.

## Supplied input

Both Track Analysis and Lap Comparison accept **Load analysis JSON**. Files stay in the browser and are never uploaded. **Use selected session** restores API evidence. Download the illustrative [example](../web/public/analysis-input-example.json) using the Example JSON link, then select that file. The example is synthetic and labelled Example Circuit.

The reusable components can also receive this contract directly as React props:

```ts
type AnalysisInput = {
  version: 1;
  track?: {
    name: string;
    lengthM: number;
    geometryKind: "observed" | "supplied";
    similarThresholdS: number;
    segments: Array<Array<{ distanceM: number; x: number; z: number }>>;
    regions: Array<{
      id: string; label: string; startM: number; endM: number;
      status: "faster" | "similar" | "slower" | "unknown";
      deltaS?: number | null;
      minimumSpeedKph?: number | null;
      referenceMinimumSpeedKph?: number | null;
      throttlePickupM?: number | null;
      referenceThrottlePickupM?: number | null;
    }>;
  };
  comparison?: {
    distanceM: number[];
    target: TraceLap;
    reference: TraceLap;
    deltaS: Array<number | null>;
    deltaMask?: boolean[];
  };
};
type TraceLap = {
  label: string;
  values: Partial<Record<"speed" | "throttle" | "brake" | "gear" | "steering", Array<number | null>>>;
  masks?: Partial<Record<"speed" | "throttle" | "brake" | "gear" | "steering", boolean[]>>;
};
```

Distances increase strictly within each path segment and within the comparison grid. All trace arrays and any masks match the distance grid length. Null values and false masks mean missing evidence. Regions use inclusive starts and exclusive ends. Overlapping regions render ambiguous overlapping map spans grey. A numeric delta must agree with its region status and configured threshold. Labels and shapes are supplied inputs; there is no hardcoded circuit registry.

Limits: 8 MB files, 20,000 geometry points, 256 segments, 128 regions and 100,000 aligned comparison samples. Geometry coordinates are bounded to ±10,000,000. The parser reports invalid inputs without replacing the last valid data.

## Recorded workflow

Choose a target, reference, comparison policy and track model using Lap Comparison's **Choose laps** controls. The front-page trace receives the validated report. **Track analysis** carries the resolved exact reference to Track Analysis, which retrieves the same pair and named region report. A single selected attempt still displays its shape and observed metrics, with comparison timing unavailable until a reference is selected.

## Verification, 2026-10-07

TypeScript and production build pass. Input/adapters, comparison identity, trajectory identity, browser live-history and pinned-poller checks pass. Browser checks at the mock's 1672×943 viewport cover local JSON loading, named region selection, original shared cursor readings, half-open windows, empty windows, gear selection and zoom/reset. Live panel rows size to their content without overlapping. The recent best timing is explicitly limited to available history rows.

The local telemetry API was offline during browser verification. Running recording/replay data could not be exercised; live unavailable-state rendering was checked. The analysis screenshots use the labelled synthetic example input, not a recorded session.

- [Lap comparison preview](../artifacts/ui-analysis/lap-comparison-1672x943.png)
- [Track analysis preview](../artifacts/ui-analysis/track-analysis-1672x943.png)
- [Live unavailable-state preview](../artifacts/ui-analysis/live-telemetry-1672x943.png)
