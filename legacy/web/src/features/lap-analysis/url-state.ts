import type { DistanceRange } from "./lap-telemetry";

// Lap Analysis URL state: `?target=&reference=&view=&region=&from=&to=`.
// `target`/`reference` are read by the server loader; the rest only seeds the
// client state and is written back with the History API (no server round trip).

export type View = "overview" | "telemetry" | "sectors" | "map" | "inputs" | "quality";
export type CompareMode = "delta" | "distance";

export const VIEWS: Array<{ key: View; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "telemetry", label: "Telemetry" },
  { key: "sectors", label: "Sectors & Corners" },
  { key: "map", label: "Track Map" },
  { key: "inputs", label: "Inputs" },
  { key: "quality", label: "Data Quality" },
];

export type InitialState = {
  view: View;
  /** Region choice id (`r:<id>` / `cx:<id>`) or null. */
  region: string | null;
  brush: DistanceRange | null;
};

type Query = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

const REGION_ID = /^(r|cx):[A-Za-z0-9._:-]{1,64}$/;

export function isView(value: unknown): value is View {
  return typeof value === "string" && VIEWS.some((view) => view.key === value);
}

/** Validated initial client state from the page's search params. */
export function parseInitialState(query: Query): InitialState {
  const view = first(query.view);
  const region = first(query.region);
  const from = Number(first(query.from));
  const to = Number(first(query.to));
  const hasRange = first(query.from) !== undefined && first(query.to) !== undefined;
  return {
    view: isView(view) ? view : "overview",
    region: region && REGION_ID.test(region) ? region : null,
    brush: hasRange && Number.isFinite(from) && Number.isFinite(to) && from >= 0 && to - from >= 10 && to < 1e6 ? { from, to } : null,
  };
}

/** Search-param patch for the client-only state (undefined values are removed). */
export function stateParams(state: { view: View; region: string | null; brush: DistanceRange | null }): Record<string, string | null> {
  return {
    view: state.view === "overview" ? null : state.view,
    region: state.region,
    from: state.brush ? String(Math.round(state.brush.from)) : null,
    to: state.brush ? String(Math.round(state.brush.to)) : null,
  };
}
