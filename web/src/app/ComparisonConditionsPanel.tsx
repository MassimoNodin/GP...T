import type {
  CarDamageObservationSummary,
  ObservedConditionAnchor,
  ObservedConditionSummary,
} from "@/lib/api";

const formatEvidenceCount = (value: number | null | undefined) =>
  typeof value === "number" ? value.toLocaleString() : "Unknown";
const conditionLabel = (value: unknown) =>
  typeof value === "string" && value
    ? value.replaceAll("_", " ").toUpperCase()
    : "UNKNOWN";
export default function ComparisonConditionsPanel({
  target,
  reference,
}: {
  target: unknown;
  reference: unknown;
}) {
  const targetSummary = safeObservedConditionSummary(target);
  const referenceSummary = safeObservedConditionSummary(reference);
  return (
    <section className="comparison-conditions panel">
      <header className="comparison-conditions-heading">
        <div>
          <span className="eyebrow">STORED TRACE EVIDENCE</span>
          <h3>Observed conditions</h3>
        </div>
        <p>
          First and last reported values within each stored trace. They do not
          guarantee lap-start or lap-end conditions, and do not establish a
          matched comparison.
        </p>
      </header>
      <div className="condition-source-grid">
        {targetSummary ? (
          <ConditionSource label="TARGET" summary={targetSummary} />
        ) : (
          <ConditionUnavailable label="TARGET" />
        )}
        {referenceSummary ? (
          <ConditionSource label="REFERENCE" summary={referenceSummary} />
        ) : (
          <ConditionUnavailable label="REFERENCE" />
        )}
      </div>
    </section>
  );
}

function ConditionUnavailable({ label }: { label: string }) {
  return (
    <article className="condition-source" role="status">
      <div className="condition-source-heading">
        <span className="eyebrow">{label}</span>
        <strong>OBSERVATIONS UNAVAILABLE</strong>
      </div>
      <p>The returned condition summary was malformed or unsupported.</p>
    </article>
  );
}

export function safeObservedConditionSummary(
  value: unknown,
): ObservedConditionSummary | null {
  const summary = record(value);
  if (
    !summary ||
    !boundedText(summary.status, 96) ||
    !boundedCount(summary.sample_count) ||
    !nullableCount(summary.matched_sample_count) ||
    !nullableCount(summary.missing_join_sample_count) ||
    !safeCountRecord(summary.unavailable_reason_counts, 64) ||
    !safeFieldCounts(summary.fields, summary.sample_count, 64) ||
    !safeFirstLast(summary.first_last_observed, 64) ||
    !(
      summary.discrete_changes === null ||
      (Array.isArray(summary.discrete_changes) &&
        summary.discrete_changes.length <= 20)
    ) ||
    !(
      summary.discrete_changes_truncated === null ||
      typeof summary.discrete_changes_truncated === "boolean"
    ) ||
    !safeCompounds(summary.distinct_compounds) ||
    !boundedText(summary.fuel_quantity_unit_note, 256) ||
    !safeEnvironment(summary.environment_context) ||
    !(
      summary.car_damage_observations == null ||
      safeCarDamage(summary.car_damage_observations)
    )
  ) {
    return null;
  }
  return value as ObservedConditionSummary;
}

function safeEnvironment(value: unknown) {
  const environment = record(value);
  if (
    !environment ||
    !boundedText(environment.status, 96) ||
    !boundedCount(environment.segment_count) ||
    !boundedCount(environment.known_segment_count) ||
    !boundedCount(environment.unknown_segment_count) ||
    !boundedCount(environment.omitted_segment_count) ||
    (environment.known_segment_count as number) +
      (environment.unknown_segment_count as number) >
      (environment.segment_count as number) ||
    (environment.omitted_segment_count as number) >
      (environment.known_segment_count as number) ||
    !record(environment.missing_value_counts) ||
    !safeCountRecord(environment.missing_value_counts, 16) ||
    !Array.isArray(environment.retained_segments) ||
    environment.retained_segments.length > 16 ||
    !environment.retained_segments.every(safeRetainedSegment) ||
    !safeDistinctValues(environment.distinct_values)
  ) {
    return false;
  }
  return true;
}

function safeRetainedSegment(value: unknown) {
  const segment = record(value);
  return Boolean(
    segment &&
    frameIdentifier(segment.from_frame_identifier) &&
    nullableCount(segment.weather_id) &&
    nullableText(segment.weather_name, 128) &&
    nullableFiniteNumber(segment.track_temperature_c) &&
    nullableFiniteNumber(segment.air_temperature_c) &&
    nullableCount(segment.formula_id),
  );
}

function safeDistinctValues(value: unknown) {
  const distinct = record(value);
  if (!distinct || Object.keys(distinct).length > 16) return false;
  return Object.values(distinct).every((entryValue) => {
    const entry = record(entryValue);
    return Boolean(
      entry &&
      Array.isArray(entry.values) &&
      entry.values.length <= 16 &&
      entry.values.every(
        (item) =>
          (typeof item === "string" && item.length <= 128) ||
          (typeof item === "number" && Number.isFinite(item)),
      ) &&
      typeof entry.truncated === "boolean",
    );
  });
}

function safeCarDamage(value: unknown) {
  const damage = record(value);
  return Boolean(
    damage &&
    boundedText(damage.version, 96) &&
    boundedText(damage.authority, 96) &&
    boundedText(damage.observation_note, 512) &&
    boundedText(damage.status, 96) &&
    boundedCount(damage.sample_count) &&
    nullableCount(damage.matched_sample_count) &&
    nullableCount(damage.missing_join_sample_count) &&
    safeCountRecord(damage.unavailable_reason_counts, 64) &&
    safeFieldCounts(damage.fields, damage.sample_count, 64) &&
    safeFirstLast(damage.first_last_observed, 64),
  );
}

function safeFieldCounts(value: unknown, sampleCount: unknown, limit: number) {
  if (value === null) return true;
  const fields = record(value);
  if (!fields || Object.keys(fields).length > limit) return false;
  return Object.values(fields).every((value) => {
    const counts = record(value);
    return Boolean(
      counts &&
      boundedCount(counts.valid_count) &&
      boundedCount(counts.missing_count) &&
      boundedCount(counts.invalid_count) &&
      (counts.valid_count as number) +
        (counts.missing_count as number) +
        (counts.invalid_count as number) <=
        (sampleCount as number),
    );
  });
}

function safeFirstLast(value: unknown, limit: number) {
  if (value === null) return true;
  const fields = record(value);
  if (!fields || Object.keys(fields).length > limit) return false;
  return Object.values(fields).every((value) => {
    const pair = record(value);
    return Boolean(
      pair &&
      (pair.first === null || safeAnchor(pair.first)) &&
      (pair.last === null || safeAnchor(pair.last)),
    );
  });
}

function safeAnchor(value: unknown) {
  const anchor = record(value);
  return Boolean(
    anchor &&
    ((typeof anchor.value === "number" && Number.isFinite(anchor.value)) ||
      typeof anchor.value === "boolean") &&
    nullableFrameIdentifier(anchor.frame_identifier) &&
    nullableFiniteNumber(anchor.session_time_s) &&
    nullableFiniteNumber(anchor.lap_distance_m),
  );
}

function safeCompounds(value: unknown) {
  if (value === null) return true;
  const compounds = record(value);
  if (!compounds) return false;
  return (["actual", "visual"] as const).every((key) => {
    const rows = compounds[key];
    return (
      Array.isArray(rows) &&
      rows.length <= 256 &&
      rows.every((value) => {
        const row = record(value);
        return Boolean(
          row &&
          boundedCount(row.raw_id) &&
          nullableCount(row.formula_id) &&
          nullableText(row.label, 128),
        );
      })
    );
  });
}

function safeCountRecord(value: unknown, limit: number) {
  if (value === null) return true;
  const counts = record(value);
  return Boolean(
    counts &&
    Object.keys(counts).length <= limit &&
    Object.values(counts).every(boundedCount),
  );
}

function boundedCount(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 10_000_000
  );
}

function nullableCount(value: unknown) {
  return value === null || boundedCount(value);
}

function frameIdentifier(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 0xffff_ffff
  );
}

function nullableFrameIdentifier(value: unknown) {
  return value === null || frameIdentifier(value);
}

function nullableFiniteNumber(value: unknown) {
  return (
    value === null || (typeof value === "number" && Number.isFinite(value))
  );
}

function boundedText(value: unknown, maxLength: number) {
  return (
    typeof value === "string" && value.length > 0 && value.length <= maxLength
  );
}

function nullableText(value: unknown, maxLength: number) {
  return (
    value === null || (typeof value === "string" && value.length <= maxLength)
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function ConditionSource({
  label: sourceLabel,
  summary,
}: {
  label: string;
  summary: ObservedConditionSummary;
}) {
  const field = (name: string) => summary.fields?.[name];
  const anchorPair = (name: string) => summary.first_last_observed?.[name];
  const conditionFields = [
    {
      title: "Fuel quantity",
      key: "fuel_in_tank_reported",
      note: summary.fuel_quantity_unit_note,
    },
    { title: "Tyre age", key: "tyre_age_laps", note: null },
  ] as const;
  const statusLine =
    summary.status === "available"
      ? `Car Status matched ${formatEvidenceCount(summary.matched_sample_count)} / ${summary.sample_count} samples · ${formatEvidenceCount(summary.missing_join_sample_count)} missing joins`
      : summary.status === "unavailable_in_trace_schema"
        ? "Car Status unavailable in this trace schema"
        : `Car Status ${conditionLabel(summary.status).toLowerCase()} · ${summary.sample_count} trace samples`;
  const compounds = summary.distinct_compounds;
  const environment = summary.environment_context;
  const retained = environment.retained_segments;
  const missingContextValues = Object.entries(environment.missing_value_counts)
    .filter(([, count]) => count > 0)
    .map(([fieldName, count]) => `${fieldName.replaceAll("_", " ")} ${count}`);
  const truncatedContextFields = Object.entries(environment.distinct_values)
    .filter(([, values]) => values.truncated)
    .map(([fieldName]) => fieldName.replaceAll("_", " "));

  return (
    <article className="condition-source">
      <div className="condition-source-heading">
        <span className="eyebrow">{sourceLabel}</span>
        <strong>{statusLine}</strong>
      </div>
      <CarDamageObservations summary={summary.car_damage_observations} />
      <div className="condition-observation-list">
        {conditionFields.map(({ title, key, note }) => {
          const counts = field(key);
          const pair = anchorPair(key);
          return (
            <div className="condition-observation" key={key}>
              <div className="condition-observation-title">
                <strong>{title}</strong>
                <span>
                  {counts
                    ? `${counts.valid_count} valid · ${counts.missing_count} missing · ${counts.invalid_count} invalid`
                    : "Field evidence unavailable"}
                </span>
              </div>
              {note && <small className="condition-measure-note">{note}</small>}
              <div className="condition-observation-values">
                {pair?.first || pair?.last ? (
                  <>
                    {pair.first && (
                      <span>
                        First {formatConditionValue(key, pair.first.value)} ·{" "}
                        {conditionAnchor(pair.first)}
                      </span>
                    )}
                    {pair.last && pair.last !== pair.first && (
                      <span>
                        Last {formatConditionValue(key, pair.last.value)} ·{" "}
                        {conditionAnchor(pair.last)}
                      </span>
                    )}
                  </>
                ) : (
                  <span>No valid reported value</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="condition-compounds">
        <span className="condition-label">COMPOUNDS</span>
        {compounds ? (
          <div className="condition-compound-list">
            {(["actual", "visual"] as const).map((kind) => (
              <div key={kind}>
                <span>{kind === "actual" ? "Actual" : "Visual"}</span>
                <div>
                  {compounds[kind].length ? (
                    compounds[kind].map((compound) => (
                      <small key={`${compound.formula_id}:${compound.raw_id}`}>
                        {compound.label ?? "Unknown label"} · ID{" "}
                        {compound.raw_id} · formula{" "}
                        {formatEvidenceCount(compound.formula_id)}
                      </small>
                    ))
                  ) : (
                    <small>Unknown</small>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <small>Unavailable in this trace schema</small>
        )}
      </div>
      <div className="condition-environment">
        <div className="condition-environment-heading">
          <span className="condition-label">SESSION CONTEXT</span>
          <span>
            {conditionLabel(environment.status)} ·{" "}
            {environment.known_segment_count} known /{" "}
            {environment.segment_count} segments
            {environment.unknown_segment_count > 0
              ? ` · ${environment.unknown_segment_count} unknown`
              : ""}
            {environment.omitted_segment_count > 0
              ? ` · ${environment.omitted_segment_count} omitted from detail`
              : ""}
          </span>
        </div>
        <div className="condition-environment-values">
          <span>
            Weather: {conditionValues(environment.distinct_values.weather_name)}
            {environment.distinct_values.weather_id?.values.length
              ? ` · IDs ${conditionValues(environment.distinct_values.weather_id)}`
              : ""}
          </span>
          <span>
            Track temperature:{" "}
            {conditionValues(
              environment.distinct_values.track_temperature_c,
              " °C",
            )}
          </span>
          <span>
            Air temperature:{" "}
            {conditionValues(
              environment.distinct_values.air_temperature_c,
              " °C",
            )}
          </span>
          <span>
            Formula IDs:{" "}
            {conditionValues(environment.distinct_values.formula_id)}
          </span>
        </div>
        {missingContextValues.length > 0 && (
          <div className="condition-environment-details">
            Missing context values by segment: {missingContextValues.join(", ")}
          </div>
        )}
        {truncatedContextFields.length > 0 && (
          <div className="condition-environment-details">
            Distinct value lists capped at 16; additional values omitted for{" "}
            {truncatedContextFields.join(", ")}.
          </div>
        )}
        {retained.length > 0 && (
          <div className="condition-context-anchors">
            {retained.length === 1 ? (
              <span>
                Context first observed at frame{" "}
                {retained[0].from_frame_identifier}
              </span>
            ) : (
              <>
                <span>
                  First retained context at frame{" "}
                  {retained[0].from_frame_identifier}
                </span>
                <span>
                  Last retained context at frame{" "}
                  {retained[retained.length - 1].from_frame_identifier}
                </span>
              </>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

function CarDamageObservations({
  summary,
}: {
  summary: CarDamageObservationSummary | undefined;
}) {
  if (!summary) return null;
  const statusLine =
    summary.status === "available"
      ? `Matched ${formatEvidenceCount(summary.matched_sample_count)} / ${summary.sample_count} exact-frame samples`
      : summary.status === "no_joined_samples"
        ? `No joined samples · ${summary.sample_count} trace samples`
        : "Unavailable in this trace schema";
  const fieldRows = Object.entries(summary.fields ?? {});

  return (
    <details className="condition-damage-observations">
      <summary>
        <span>
          <span className="condition-label">
            PRIMARY PLAYER · SPARSE DIAGNOSTIC
          </span>
          <strong>{statusLine}</strong>
        </span>
        <span aria-hidden="true">{fieldRows.length} fields</span>
      </summary>
      <p>{summary.observation_note}</p>
      {fieldRows.length ? (
        <div className="condition-damage-table-wrap">
          <table>
            <thead>
              <tr>
                <th>FIELD</th>
                <th>VALID</th>
                <th>MISSING</th>
                <th>INVALID</th>
                <th>FIRST OBSERVED</th>
                <th>LAST OBSERVED</th>
              </tr>
            </thead>
            <tbody>
              {fieldRows.map(([field, counts]) => {
                const observed = summary.first_last_observed?.[field];
                return (
                  <tr key={field}>
                    <td>{field.replaceAll("_", " ").toUpperCase()}</td>
                    <td>{counts.valid_count}</td>
                    <td>{counts.missing_count}</td>
                    <td>{counts.invalid_count}</td>
                    <td>{damageObservation(observed?.first ?? null)}</td>
                    <td>{damageObservation(observed?.last ?? null)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <small>Field values are unavailable for this trace schema.</small>
      )}
      <small className="condition-damage-reasons">
        Unavailable exact-frame joins:{" "}
        {Object.entries(summary.unavailable_reason_counts ?? {})
          .filter(([, count]) => count > 0)
          .map(([reason, count]) => `${reason.replaceAll("_", " ")} ${count}`)
          .join(" · ") || "none reported"}
      </small>
    </details>
  );
}

function damageObservation(anchor: ObservedConditionAnchor | null) {
  if (!anchor) return "—";
  const value =
    typeof anchor.value === "boolean"
      ? anchor.value
        ? "Yes"
        : "No"
      : `${anchor.value}%`;
  return `${value} ${conditionAnchor(anchor)}`;
}

function formatConditionValue(field: string, value: number | boolean) {
  if (typeof value === "boolean") return value ? "On" : "Off";
  return field === "tyre_age_laps" ? String(value) : value.toFixed(3);
}

function conditionAnchor(anchor: ObservedConditionAnchor) {
  const parts = [
    anchor.frame_identifier == null ? null : `frame ${anchor.frame_identifier}`,
    anchor.session_time_s == null
      ? null
      : `${anchor.session_time_s.toFixed(3)} s session time`,
    anchor.lap_distance_m == null
      ? null
      : `${anchor.lap_distance_m.toFixed(1)} m distance`,
  ].filter((part): part is string => part !== null);
  return parts.length ? `@ ${parts.join(" · ")}` : "source anchor unavailable";
}

function conditionValues(
  entry: { values: Array<string | number>; truncated: boolean } | undefined,
  suffix = "",
) {
  if (!entry?.values.length) return "Unknown";
  const values = entry.values.map((value) => `${value}${suffix}`).join(", ");
  return entry.truncated
    ? `${values} (list capped at 16; more omitted)`
    : values;
}
