import type { EnrichmentView, ChartMetric, SummaryView } from "./overview-data";
import type { OverviewEvent, OverviewLap, OverviewModel } from "./types";

/** Everything a tab view renders, resolved from the model and the browser's deferred reads. */
export type ViewData = {
  model: OverviewModel;
  /** Laps with sectors and tyres merged in once `/laps` has loaded. */
  laps: OverviewLap[];
  enrichment: EnrichmentView;
  summary: SummaryView;
  events: OverviewEvent[];
  selectedId: string;
  /** Compare mode only: the resolved reference lap id. */
  referenceId: string | null;
  best: OverviewLap | null;
  metric: ChartMetric;
};
