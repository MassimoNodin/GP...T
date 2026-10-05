import type { PlayerCarSetupContext, PlayerCarSetupSnapshot } from "@/lib/api";

type NumericSetupField = Exclude<
  keyof PlayerCarSetupSnapshot,
  "invalid_fields"
>;

const SETUP_FIELDS: Array<[NumericSetupField, string]> = [
  ["front_wing", "Front wing"],
  ["rear_wing", "Rear wing"],
  ["on_throttle_differential", "On-throttle differential"],
  ["off_throttle_differential", "Off-throttle differential"],
  ["front_camber", "Front camber"],
  ["rear_camber", "Rear camber"],
  ["front_toe", "Front toe"],
  ["rear_toe", "Rear toe"],
  ["front_suspension", "Front suspension"],
  ["rear_suspension", "Rear suspension"],
  ["front_anti_roll_bar", "Front anti-roll bar"],
  ["rear_anti_roll_bar", "Rear anti-roll bar"],
  ["front_suspension_height", "Front suspension height"],
  ["rear_suspension_height", "Rear suspension height"],
  ["brake_pressure_percent", "Brake pressure (%)"],
  ["brake_bias_percent", "Brake bias (%)"],
  ["engine_braking_percent", "Engine braking (%)"],
  ["ballast", "Ballast value"],
  ["fuel_load", "Setup fuel load"],
  ["front_left_tyre_pressure_psi", "Front left tyre (psi)"],
  ["front_right_tyre_pressure_psi", "Front right tyre (psi)"],
  ["rear_left_tyre_pressure_psi", "Rear left tyre (psi)"],
  ["rear_right_tyre_pressure_psi", "Rear right tyre (psi)"],
];

function statusLabel(status: PlayerCarSetupContext["status"] | undefined) {
  switch (status) {
    case "observed":
      return "OBSERVED";
    case "observed_unchanged":
      return "SAME REPORTED VALUES";
    case "observed_changed":
      return "REPORTED VALUES CHANGED";
    case "incomplete":
      return "INCOMPLETE";
    default:
      return "UNKNOWN";
  }
}

function sourceNumber(
  source: Record<string, unknown> | null | undefined,
  key: string,
) {
  const value = source?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function settingValue(value: number | null) {
  if (value === null) return "—";
  return Number.isInteger(value)
    ? String(value)
    : String(Number(value.toFixed(2)));
}

function SetupSummary({
  heading,
  report,
}: {
  heading: string;
  report: PlayerCarSetupContext | null | undefined;
}) {
  const start = report?.at_start;
  const setup = start?.status === "reported" ? start.setup : null;
  const source = start?.source;
  const ageFrames = sourceNumber(source, "age_frames");

  return (
    <div className="participant-context-column setup-context-column">
      <div className="participant-context-column-heading">
        <span>{heading}</span>
        <strong>{statusLabel(report?.status)}</strong>
      </div>
      {setup ? (
        <>
          <div className="setup-context-fields">
            {SETUP_FIELDS.map(([field, label]) => (
              <div className="setup-context-field" key={field}>
                <span>{label}</span>
                <strong>{settingValue(setup[field])}</strong>
              </div>
            ))}
          </div>
          {start?.next_front_wing_value != null ? (
            <div className="setup-next-wing">
              <span>Requested next-pit front-wing game value</span>
              <strong>{settingValue(start.next_front_wing_value)}</strong>
            </div>
          ) : null}
          <small className="participant-context-source">
            Frame {sourceNumber(source, "frame_ordinal") ?? "unknown"}
            {ageFrames === null
              ? ""
              : ` · ${ageFrames} admitted frames before lap start`}
          </small>
          {setup.invalid_fields.length ? (
            <small className="participant-context-source">
              Non-finite game values omitted: {setup.invalid_fields.join(", ")}
            </small>
          ) : null}
        </>
      ) : (
        <p className="participant-context-unknown">
          {start?.reason ??
            report?.reason ??
            "No safe setup snapshot was available at lap start."}
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

export default function PlayerCarSetupContextPanel({
  target,
  reference,
}: {
  target: PlayerCarSetupContext | null | undefined;
  reference?: PlayerCarSetupContext | null;
}) {
  const safeTarget = safeCarSetupContext(target) ? target : null;
  const safeReference =
    reference === undefined
      ? undefined
      : safeCarSetupContext(reference)
        ? reference
        : null;
  return (
    <section
      className="participant-context panel"
      aria-label="Game-reported player car setup"
    >
      <div className="quality-panel-heading">
        <div>
          <span className="eyebrow">CAR SETUP EVIDENCE</span>
          <h2>
            {reference === undefined
              ? "Reported setup"
              : "Compare reported setups"}
          </h2>
        </div>
        <span className="quality-state">{statusLabel(safeTarget?.status)}</span>
      </div>
      <div
        className={`participant-context-grid ${safeReference === undefined ? "single" : ""}`}
      >
        <SetupSummary heading="TARGET" report={safeTarget} />
        {safeReference !== undefined ? (
          <SetupSummary heading="REFERENCE" report={safeReference} />
        ) : null}
      </div>
      <p className="participant-context-warning">
        Game-reported snapshots show settings at observed frames; they do not
        prove uninterrupted setup state between observations. Setup fuel load is
        separate from current fuel mass. Requested next-pit front-wing value is
        separate from the current front-wing setting.
      </p>
    </section>
  );
}

export function safeCarSetupContext(value: unknown): value is PlayerCarSetupContext {
  const context = record(value);
  const start = record(context?.at_start);
  const setup = start?.setup;
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
      (setup == null || safeSetupSnapshot(setup)) &&
      (start.source == null || record(start.source)) &&
      nullableFiniteNumber(start.next_front_wing_value) &&
      optionalText(start.reason, 240) &&
      optionalText(context.reason, 240) &&
      optionalTextArray(context.reasons, 16) &&
      (!context.limits || record(context.limits)),
  );
}

function safeSetupSnapshot(value: unknown) {
  const setup = record(value);
  return Boolean(
    setup &&
      SETUP_FIELDS.every(([field]) => {
        const value = setup[field];
        return value === null || (typeof value === "number" && Number.isFinite(value));
      }) &&
      Array.isArray(setup.invalid_fields) &&
      setup.invalid_fields.length <= 32 &&
      setup.invalid_fields.every((field) => boundedText(field, 96)),
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

function nullableFiniteNumber(value: unknown) {
  return (
    value === undefined ||
    value === null ||
    (typeof value === "number" && Number.isFinite(value))
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
