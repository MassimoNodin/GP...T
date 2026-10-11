import { RouteNotFound } from "@/features/route-states/RouteNotFound";

export default function SessionNotFound() {
  return (
    <RouteNotFound
      active="review"
      title="Session not found"
      message="We could not find a session with that ID. It may have been removed, or the link may be incomplete."
      href="/review"
      linkLabel="Back to Session Library"
    />
  );
}
