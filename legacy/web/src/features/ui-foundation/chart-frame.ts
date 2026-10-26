"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

/**
 * Charts are drawn in real pixels at their container's width, so text, dots and
 * strokes keep their size and wide screens add horizontal resolution instead
 * of scaling everything up.
 */

/** Width in px of an element, tracked with a ResizeObserver; `fallback` until measured. */
export function useElementWidth<T extends HTMLElement>(fallback = 470): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = (value: number) => {
      const next = Math.round(value);
      if (next > 0) setWidth(next);
    };
    measure(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) measure(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Plot height for a single chart: bounded so an ultra-wide card is not also tall. */
export function chartHeight(width: number, min = 190, max = 250): number {
  return Math.min(max, Math.max(min, Math.round(width * 0.42)));
}
