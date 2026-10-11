// Pure helpers that turn v2 session-evidence records into the Drive Session
// Context. No runtime imports so they can be unit tested directly.

export type EvidenceSession = {
  id: string;
  lifecycle: string;
  acquisition: string;
  context: unknown;
};

export type SessionContextModel = {
  title: string;
  subtitle: string[];
  track: string;
  facts: Array<[string, string]>;
  /** Short footer description: [circuit, "Practice 1 • Ended"]. */
  footerLines: string[];
  /** Header context items. */
  headerItems: string[];
  /** True when the session is the one the receiver is serving now. */
  current: boolean;
};

const LIFECYCLE: Record<string, string> = {
  active: "Live",
  ended: "Ended",
  interrupted: "Interrupted",
};

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function parseSessionContext(value: unknown): Record<string, unknown> | null {
  if (typeof value === "string") {
    try {
      return record(JSON.parse(value));
    } catch {
      return null;
    }
  }
  return record(value);
}

export function titleCase(raw: string): string {
  return raw
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function formatGame(packetFormat: unknown): string | null {
  const format = finiteNumber(packetFormat);
  if (format === null || !Number.isInteger(format) || format < 2000 || format > 2099) return null;
  return `F1 ${format % 100}`;
}

/**
 * The session the receiver is serving now: an active one first, then the one
 * the receiver status names, otherwise the last listed. The sessions endpoint
 * carries no timestamps, so the final fallback is "a recent session", not
 * strictly the newest.
 */
export function pickCurrentSession(
  sessions: EvidenceSession[],
  statusSessionId: string | null,
): { session: EvidenceSession; current: boolean } | null {
  if (sessions.length === 0) return null;
  const active = [...sessions].reverse().find((item) => item.lifecycle === "active");
  if (active) return { session: active, current: true };
  const named = statusSessionId ? sessions.find((item) => item.id === statusSessionId) : undefined;
  if (named) return { session: named, current: named.acquisition === "receiving" };
  return { session: sessions[sessions.length - 1], current: false };
}

export function sessionContextModel(
  picked: { session: EvidenceSession; current: boolean },
  trackKeyFromName: (name: string | null | undefined) => string,
): SessionContextModel | null {
  const context = parseSessionContext(picked.session.context);
  const trackName = text(context?.track_name);
  if (!context || !trackName) return null;

  const type = text(context.session_type);
  const typeLabel = type ? titleCase(type) : "Session";
  const lifecycle = picked.current ? "Live" : (LIFECYCLE[picked.session.lifecycle] ?? "Ended");
  const facts: Array<[string, string]> = [];

  const game = formatGame(context.packet_format);
  if (game) facts.push(["Game", game]);

  const weather = text(context.weather_name);
  const air = finiteNumber(context.air_temperature_c);
  const conditions = [weather ? titleCase(weather) : null, air !== null ? `${Math.round(air)}°C` : null]
    .filter(Boolean)
    .join(", ");
  if (conditions) facts.push(["Conditions", conditions]);

  const trackTemp = finiteNumber(context.track_temperature_c);
  if (trackTemp !== null) facts.push(["Track temp", `${Math.round(trackTemp)}°C`]);

  const progress = record(context.progress);
  const totalLaps = finiteNumber(context.total_laps) ?? finiteNumber(progress?.total_laps);
  const durationS = finiteNumber(progress?.duration_s);
  if (type?.includes("race") && totalLaps !== null && totalLaps > 1) {
    facts.push(["Laps", String(totalLaps)]);
  } else if (durationS !== null && durationS > 0) {
    facts.push(["Duration", `${Math.round(durationS / 60)} min`]);
  }

  const length = finiteNumber(context.track_length_m);
  if (length !== null && length > 0) facts.push(["Length", `${(length / 1000).toFixed(3)} km`]);

  return {
    title: trackName,
    subtitle: [typeLabel, lifecycle],
    track: trackKeyFromName(trackName),
    facts,
    footerLines: [trackName, `${typeLabel}  •  ${lifecycle}`],
    headerItems: [trackName, typeLabel],
    current: picked.current,
  };
}
