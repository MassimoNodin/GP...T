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
          {statusLabel(target?.status)}
        </span>
      </div>
      <div className={`participant-context-grid ${reference === undefined ? "single" : ""}`}>
        <ParticipantSummary heading="TARGET" report={target} />
        {reference !== undefined ? (
          <ParticipantSummary heading="REFERENCE" report={reference} />
        ) : null}
      </div>
      <p className="participant-context-warning">
        Game-reported session metadata only. Matching IDs do not prove the same
        person, car setup, or performance conditions.
      </p>
    </section>
  );
}
