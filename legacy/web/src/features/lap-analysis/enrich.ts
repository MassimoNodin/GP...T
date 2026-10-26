import { humanReason } from "@/features/review-data/reasons";
import type { LapEnrichment } from "@/features/review-data/enrichment";
import type { AnalysisLap } from "./load";

// Merges the v2 `/laps` enrichment (sectors, tyres) into the lap list. Pure; the
// server loader and the client fallback both use it.

/** Laps with sector times and tyre data from the enrichment, matched by revision id. */
export function mergeEnrichment(laps: readonly AnalysisLap[], enrichment: LapEnrichment): AnalysisLap[] {
  const byRevision = new Map(enrichment.laps.map((lap) => [lap.revisionId, lap]));
  return laps.map((lap) => {
    const found = lap.revisionId ? byRevision.get(lap.revisionId) : undefined;
    if (!found) return lap;
    const tyre = found.tyre
      ? {
          label: found.tyre.visualLabel ?? found.tyre.actualLabel ?? "Unknown",
          actual: found.tyre.actualLabel,
          ageLaps: found.tyre.ageLaps,
        }
      : null;
    return {
      ...lap,
      sectors: found.sectorsMs ?? lap.sectors,
      sectorsSource: found.sectorsMs ? found.sectorsSource : (lap.sectorsSource ?? null),
      sectorsReason: found.sectorsMs ? null : humanReason(found.sectorsReason) || null,
      tyre,
      tyreReason: tyre ? null : humanReason(found.tyreReason) || null,
    };
  });
}

/** Distinct sector reasons across laps, for a footnote. */
export function sectorReasons(laps: readonly AnalysisLap[]): string[] {
  return [...new Set(laps.filter((lap) => !lap.sectors && lap.sectorsReason).map((lap) => lap.sectorsReason as string))];
}
