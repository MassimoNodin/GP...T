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
      active="settings"
      error={error}
      retry={retry}
      backHref="/drive"
      backLabel="Go to Drive"
    />
  );
}
