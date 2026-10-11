// Circuit outlines drawn as closed vector loops. Only circuits with an outline
// render a map; others fall back to a neutral generic loop.
const OUTLINES: Record<string, { viewBox: string; d: string }> = {
  monaco: {
    viewBox: "0 0 120 80",
    d: "M9.5 71.5c-3.6-3.2-2.4-8.4 1.8-10.6l17.4-9.1c5.2-2.7 7.3-8.6 12.4-11.7 3.9-2.4 8.7-2.3 13.1-3.4l14.6-3.6c5.2-1.3 8.6-5.6 12.8-8.5l12.3-8.5c2.9-2 6.4-6.6 9.6-6 2.5.5 1.8 3.6 2.9 4.6 1.3 1.2 4.3.4 4 2.6-.3 2.1-3.4 2-4.9 3.1-1.8 1.4-1.1 4.2-2.7 5.7-2.6 2.4-6.4 2.4-9.6 3.8l-14.2 6.4c-4.9 2.2-10.4 2.6-15.6 3.9-4.1 1-8.2 1.2-11.4 4.1-3.6 3.3-4.9 8.5-8.6 11.8-3.2 2.9-7.4 4.4-11 6.8-3.4 2.3-5.6 6.4-9.8 7.4-2.4.6-5.3.9-7.1-.8z",
  },
  generic: {
    viewBox: "0 0 120 80",
    d: "M14 58c-6-8-4-22 6-28l24-14c10-6 22-6 32 0l26 14c8 5 10 16 4 24l-6 8c-5 6-14 9-22 7l-18-4c-6-1-12 0-17 3l-12 6c-7 3-14-1-17-6z",
  },
};

export function TrackMap({
  track,
  className,
  strokeWidth = 2.2,
  title,
}: {
  track: string;
  className?: string;
  strokeWidth?: number;
  title?: string;
}) {
  const outline = OUTLINES[track] ?? OUTLINES.generic;
  const labelled = typeof title === "string" && title.length > 0;
  return (
    <svg
      className={className}
      viewBox={outline.viewBox}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinejoin="round"
      strokeLinecap="round"
      focusable="false"
      role={labelled ? "img" : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-label={labelled ? title : undefined}
    >
      <path d={outline.d} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function trackKeyFromName(name: string | null | undefined): string {
  if (!name) return "generic";
  return (
    name
      .toLowerCase()
      .replace(/grand prix|gp/g, "")
      .trim()
      .split(/\s+/)[0]
      ?.replace(/[^a-z0-9-]/g, "") || "generic"
  );
}
