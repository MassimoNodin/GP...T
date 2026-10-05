import type {
  PlayerParticipantContext,
} from "@/lib/api";

function statusLabel(status: PlayerParticipantContext["status"] | undefined) {
  switch (status) {
    case "observed":
      return "OBSERVED";
    case "observed_unchanged":
      return "SAME REPORTED FIELDS";
    case "observed_changed":
      return "REPORTED FIELDS CHANGED";
    case "incomplete":
      return "INCOMPLETE";
    default:
      return "UNKNOWN";
  }
}

function sourceNumber(source: Record<string, unknown> | null | undefined, key: string) {
  const value = source?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function ParticipantSummary({
  heading,
  report,
}: {
  heading: string;
  report: PlayerParticipantContext | null | undefined;
}) {
  const start = report?.at_start;
  const participant =
    start?.status === "reported" ? start.participant : null;
  const source = start?.source;
  const ageFrames = sourceNumber(source, "age_frames");

  return (
    <div className="participant-context-column">
      <div className="participant-context-column-heading">
        <span>{heading}</span>
        <strong>{statusLabel(report?.status)}</strong>
      </div>
      {participant ? (
        <>
          <strong className="participant-context-name">
            {participant.name || "Name not reported"}
          </strong>
          <div className="participant-context-fields">
            <span>Team ID</span>
            <strong>{participant.team_id}</strong>
            <span>Driver ID</span>
            <strong>{participant.driver_id}</strong>
            <span>Network ID</span>
            <strong>{participant.network_id}</strong>
            <span>My Team</span>
            <strong>{participant.my_team ? "Yes" : "No"}</strong>
          </div>
          <small className="participant-context-source">
            Frame {sourceNumber(source, "frame_ordinal") ?? "unknown"}
            {ageFrames === null ? "" : ` · ${ageFrames} admitted frames before lap start`}
          </small>
        </>
      ) : (
        <p className="participant-context-unknown">
          {start?.reason ?? report?.reason ?? "No safe snapshot was available at lap start."}
        </p>
      )}
      {report && report.observed_change_count > 0 ? (
        <small className="participant-context-change">
          {report.observed_change_count} reported change
          {report.observed_change_count === 1 ? "" : "s"} during this attempt
        </small>
      ) : null}
    </div>
  );
}

export default function PlayerParticipantContextPanel({
  target,
  reference,
}: {
  target: PlayerParticipantContext | null | undefined;
  reference?: PlayerParticipantContext | null;
}) {
  const safeTarget = safeParticipantContext(target) ? target : null;
  const safeReference =
    reference === undefined
      ? undefined
      : safeParticipantContext(reference)
        ? reference
        : null;
  return (
    <section
      className="participant-context panel"
      aria-label="Game-reported player participant context"
    >
      <div className="quality-panel-heading">
        <div>
          <span className="eyebrow">PARTICIPANT EVIDENCE</span>
          <h2>{reference === undefined ? "Player context" : "Compare reported context"}</h2>
        </div>
        <span className="quality-state">
          {statusLabel(safeTarget?.status)}
        </span>
      </div>
      <div className={`participant-context-grid ${safeReference === undefined ? "single" : ""}`}>
        <ParticipantSummary heading="TARGET" report={safeTarget} />
        {safeReference !== undefined ? (
          <ParticipantSummary heading="REFERENCE" report={safeReference} />
        ) : null}
      </div>
      <p className="participant-context-warning">
        Game-reported session metadata only. Matching IDs do not prove the same
        person, car setup, or performance conditions.
      </p>
    </section>
  );
}

export function safeParticipantContext(value: unknown): value is PlayerParticipantContext {
  const context = record(value);
  const start = record(context?.at_start);
  const participant = start?.participant;
  return Boolean(
    context &&
      context.schema_version === 1 &&
      context.continuity_claim === false &&
      safeStatus(context.status) &&
      record(context.scope) &&
      Array.isArray(context.observations) &&
      context.observations.length <= 16 &&
      optionalCount(context.observation_count) &&
      boundedCount(context.observed_change_count) &&
      boundedCount(context.unknown_event_count) &&
      boundedCount(context.observations_omitted_count) &&
      start &&
      (start.status === "reported" || start.status === "unknown") &&
      (participant == null || safeParticipantSnapshot(participant)) &&
      (start.source == null || record(start.source)) &&
      optionalText(start.reason, 240) &&
      optionalText(context.reason, 240) &&
      optionalTextArray(context.reasons, 16) &&
      (!context.limits || record(context.limits)),
  );
}

function safeParticipantSnapshot(value: unknown) {
  const participant = record(value);
  return Boolean(
    participant &&
      typeof participant.name === "string" &&
      participant.name.length <= 128 &&
      [
        "driver_id",
        "network_id",
        "team_id",
        "race_number",
        "nationality_id",
        "your_telemetry",
        "tech_level",
        "platform_id",
      ].every((field) => Number.isSafeInteger(participant[field])) &&
      ["ai_controlled", "my_team"].every(
        (field) => typeof participant[field] === "boolean",
      ),
  );
}

function safeStatus(value: unknown) {
  return typeof value === "string" && [
    "observed",
    "observed_unchanged",
    "observed_changed",
    "unknown",
    "incomplete",
  ].includes(value);
}

function optionalCount(value: unknown) {
  return value === undefined || boundedCount(value);
}

function boundedCount(value: unknown) {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 1_000_000
  );
}

function boundedText(value: unknown, limit: number) {
  return typeof value === "string" && value.length > 0 && value.length <= limit;
}

function optionalText(value: unknown, limit: number) {
  return value === undefined || value === null || boundedText(value, limit);
}

function optionalTextArray(value: unknown, limit: number) {
  return (
    value === undefined ||
    (Array.isArray(value) &&
      value.length <= limit &&
      value.every((item) => boundedText(item, 128)))
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
