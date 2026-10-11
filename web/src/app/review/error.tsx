"use client";

import { RouteError } from "@/features/route-states/RouteError";

export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <RouteError
      active="review"
      error={error}
      retry={retry}
      title="Session Library unavailable"
      message="The session library could not be loaded. Check that the local backend is running, then try again."
      backHref="/drive"
      backLabel="Go to Drive"
    />
  );
}
