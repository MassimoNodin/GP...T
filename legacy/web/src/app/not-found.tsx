import { RouteNotFound } from "@/features/route-states/RouteNotFound";

export default function NotFound() {
  return (
    <RouteNotFound
      active="drive"
      title="Page not found"
      message="This page does not exist. Use the navigation to get back to a screen."
      href="/drive"
      linkLabel="Go to Drive"
    />
  );
}
