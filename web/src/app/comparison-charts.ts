import type { Comparison, LapRecord } from "@/lib/api";
import type { ChartSpec } from "./LinkedComparisonCharts";

export function buildComparisonCharts(
  comparison: Comparison,
  target: LapRecord | null,
  reference: LapRecord | null,
): ChartSpec[] {
  return [
    {
      id: "speed",
      title: "Speed trace",
      subtitle: "Vehicle speed · km/h",
      unit: "KM/H",
      valueUnit: "km/h",
      series: [
        {
          label: `Target · ${lapTime(target?.lap_time_ms)}`,
          color: "#f06a4f",
          values: comparison.target_trace.values.speed_mps.map((value) =>
            typeof value === "number" ? value * 3.6 : null,
          ),
          mask: comparison.target_trace.masks.speed_mps,
        },
        {
          label: `Reference · ${lapTime(reference?.lap_time_ms)}`,
          color: "#71c7b5",
          values: comparison.reference_trace.values.speed_mps.map((value) =>
            typeof value === "number" ? value * 3.6 : null,
          ),
          mask: comparison.reference_trace.masks.speed_mps,
        },
      ],
      differences: [
        { label: "Target − reference", left: 0, right: 1, unit: "km/h" },
      ],
    },
    {
      id: "delta",
      title: "Lap delta",
      subtitle: "Positive means target is slower · seconds",
      unit: "SECONDS",
      valueUnit: "s",
      signedValues: true,
      precision: 3,
      zero: true,
      series: [
        {
          label: "Target − reference",
          color: "#f0b45c",
          values: comparison.delta_s,
          mask: comparison.delta_mask,
        },
      ],
    },
    {
      id: "inputs",
      title: "Driver inputs",
      subtitle: "Brake and throttle · percent",
      unit: "%",
      valueUnit: "%",
      valueScale: 100,
      range: [0, 1],
      percentAxis: true,
      series: [
        {
          label: "Target brake",
          color: "#f06a4f",
          values: numbers(comparison.target_trace.values.brake),
          mask: comparison.target_trace.masks.brake,
        },
        {
          label: "Target throttle",
          color: "#71c7b5",
          values: numbers(comparison.target_trace.values.throttle),
          mask: comparison.target_trace.masks.throttle,
        },
        {
          label: "Reference brake",
          color: "#d89079",
          values: numbers(comparison.reference_trace.values.brake),
          mask: comparison.reference_trace.masks.brake,
        },
        {
          label: "Reference throttle",
          color: "#97b2a9",
          values: numbers(comparison.reference_trace.values.throttle),
          mask: comparison.reference_trace.masks.throttle,
        },
      ],
      differences: [
        {
          label: "Brake · target − reference",
          left: 0,
          right: 2,
          unit: "percentage points",
          scale: 100,
        },
        {
          label: "Throttle · target − reference",
          left: 1,
          right: 3,
          unit: "percentage points",
          scale: 100,
        },
      ],
    },
    {
      id: "steering",
      optional: true,
      overlayLabel: "Steering input",
      title: "Steering input",
      subtitle: "Signed normalized player input · not steering angle",
      unit: "NORMALIZED",
      valueUnit: "input",
      range: [-1, 1],
      signedValues: true,
      precision: 2,
      series: [
        {
          label: "Target steering input",
          color: "#f06a4f",
          values: channel(comparison.target_trace, "steering", comparison),
          mask: channelMask(comparison.target_trace, "steering", comparison),
        },
        {
          label: "Reference steering input",
          color: "#71c7b5",
          values: channel(comparison.reference_trace, "steering", comparison),
          mask: channelMask(comparison.reference_trace, "steering", comparison),
        },
      ],
      differences: [
        {
          label: "Target − reference",
          left: 0,
          right: 1,
          unit: "input",
          precision: 2,
        },
      ],
    },
    {
      id: "gear",
      optional: true,
      overlayLabel: "Gear",
      title: "Gear changes",
      subtitle: "Discrete reported gear · shifts shown as steps",
      unit: "GEAR",
      valueUnit: "gear",
      valueFormat: "gear",
      renderMode: "step",
      range: [-1, 8],
      series: [
        {
          label: "Target gear",
          color: "#f06a4f",
          values: channel(comparison.target_trace, "gear", comparison),
          mask: channelMask(comparison.target_trace, "gear", comparison),
        },
        {
          label: "Reference gear",
          color: "#71c7b5",
          values: channel(comparison.reference_trace, "gear", comparison),
          mask: channelMask(comparison.reference_trace, "gear", comparison),
        },
      ],
    },
  ];
}

function channel(
  trace: Comparison["target_trace"],
  name: string,
  comparison: Comparison,
) {
  const values = trace.values[name];
  return Array.isArray(values) && values.length === comparison.distance_m.length
    ? numbers(values)
    : Array.from({ length: comparison.distance_m.length }, () => null);
}

function numbers(values: Array<number | boolean | null>) {
  return values.map((value) => (typeof value === "number" ? value : null));
}

function channelMask(
  trace: Comparison["target_trace"],
  name: string,
  comparison: Comparison,
) {
  const mask = trace.masks[name];
  return Array.isArray(mask) && mask.length === comparison.distance_m.length
    ? mask
    : Array.from({ length: comparison.distance_m.length }, () => false);
}

function lapTime(ms: number | null | undefined) {
  return ms == null
    ? "No official time"
    : `${Math.floor(ms / 60_000)}:${((ms % 60_000) / 1000).toFixed(3).padStart(6, "0")}`;
}
