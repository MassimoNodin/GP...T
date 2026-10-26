// Settings tabs and the `?section=` deep link. Pure so the server page and the
// client screen share one definition.

export type Section = "assistance" | "voice" | "connections" | "data" | "advanced";

export const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: "assistance", label: "Assistance" },
  { key: "voice", label: "Voice & Controls" },
  { key: "connections", label: "Connections" },
  { key: "data", label: "Data & Storage" },
  { key: "advanced", label: "Advanced Tools" },
];

export const DEFAULT_SECTION: Section = "assistance";

/** Reads `?section=`. Repeated, unknown or missing values fall back to Assistance. */
export function parseSection(value: string | string[] | null | undefined): Section {
  if (typeof value !== "string") return DEFAULT_SECTION;
  const match = SECTIONS.find((section) => section.key === value);
  return match ? match.key : DEFAULT_SECTION;
}

/** URL for a tab: the default tab keeps the clean `/settings` address. */
export function sectionHref(section: Section): string {
  return section === DEFAULT_SECTION ? "/settings" : `/settings?section=${section}`;
}

/** `search` with `section` replaced (other parameters are kept). */
export function searchWithSection(search: string, section: Section): string {
  const params = new URLSearchParams(search);
  if (section === DEFAULT_SECTION) params.delete("section");
  else params.set("section", section);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function panelId(section: Section): string {
  return `settings-panel-${section}`;
}

export const TAB_ID_PREFIX = "settings-tab";
