"use client";

import { useSearchParams } from "next/navigation";
import { SkeletonBody, type SkeletonVariant } from "./Skeleton";

/**
 * Skeleton chosen by a query parameter (`?view=`, `?section=`), so a reload or
 * link into a tab shows that tab's layout while the server loader runs.
 * Unknown or missing values use `fallback`.
 */
export function QueryVariantBody({
  param,
  variants,
  fallback,
}: {
  param: string;
  variants: Readonly<Record<string, SkeletonVariant>>;
  fallback: SkeletonVariant;
}) {
  const value = useSearchParams().get(param);
  const variant = value !== null && Object.hasOwn(variants, value) ? variants[value] : fallback;
  return <SkeletonBody variant={variant} />;
}
