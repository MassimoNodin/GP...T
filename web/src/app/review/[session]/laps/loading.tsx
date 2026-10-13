import { QueryVariantBody } from "@/features/route-states/QueryVariantBody";
import { RouteLoading } from "@/features/route-states/RouteLoading";

// Keys of `?view=` on Lap Analysis.
const VIEWS = {
  telemetry: "laps-charts",
  inputs: "laps-charts",
  sectors: "laps-table",
  quality: "laps-table",
  map: "laps-map",
} as const;

export default function Loading() {
  return (
    <RouteLoading
      active="review"
      variant="laps"
      body={<QueryVariantBody param="view" variants={VIEWS} fallback="laps" />}
    />
  );
}
