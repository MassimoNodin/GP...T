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
      title="Session unavailable"
      message="This session could not be loaded. Try again, or return to the session library."
      backHref="/review"
      backLabel="Back to Session Library"
    />
  );
}
