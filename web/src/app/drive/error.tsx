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
      active="drive"
      error={error}
      retry={retry}
      backHref="/review"
      backLabel="Open Session Library"
    />
  );
}
