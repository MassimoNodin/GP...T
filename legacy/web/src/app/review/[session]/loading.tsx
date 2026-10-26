import { QueryVariantBody } from "@/features/route-states/QueryVariantBody";
import { RouteLoading } from "@/features/route-states/RouteLoading";

// Keys of `?view=` on the session overview.
const VIEWS = {
  laps: "overview-table",
  stints: "overview-table",
  events: "overview-events",
  setup: "overview-setup",
} as const;

export default function Loading() {
  return (
    <RouteLoading
      active="review"
      variant="overview"
      body={<QueryVariantBody param="view" variants={VIEWS} fallback="overview" />}
    />
  );
}
