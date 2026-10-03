import type {
  ObservedPositionProbe,
  ObservedTrajectoryComparisonPreview,
} from "@/lib/api";

export default function TrajectoryComparisonPanel({
  report,
  unavailableReason,
  probeDistanceM,
  formParams,
}: {
  report: ObservedTrajectoryComparisonPreview | null;
  unavailableReason: string | null;
  probeDistanceM: string;
  formParams: Record<string, string>;
}) {
  const probeForm = (
    <form className="trajectory-probe-form" action="/" method="get">
      {Object.entries(formParams).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      <label htmlFor="position-probe-m">Position probe · lap distance (m)</label>
      <input
        id="position-probe-m"
        name="position_probe_m"
        type="number"
        min="0"
        step="any"
        inputMode="decimal"
        placeholder="e.g. 1250"
        defaultValue={probeDistanceM}
      />
      <button type="submit">Inspect distance</button>
    </form>
  );

  if (!report) {
    return (
      <section className="trajectory-comparison-unavailable panel">
        <div className="eyebrow">OBSERVED PATH COMPARISON</div>
        <p>
          {unavailableReason
            ? unavailableReason.replaceAll("_", " ")
            : "Paired path evidence is unavailable for this lap pair."}
        </p>
        <small>The ordinary lap comparison remains available.</small>
        {probeForm}
      </section>
    );
  }

  const [minX, maxX] = report.plot_bounds_world_xz_m.world_x;
  const [minZ, maxZ] = report.plot_bounds_world_xz_m.world_z;
  const side = 800;
  const span = maxX - minX;
  const x = (value: number) => ((value - minX) / span) * side;
  const y = (value: number) => ((maxZ - value) / span) * side;
  const positionProbe = report.position_probe;

  return (
    <section
      className="trajectory-comparison panel"
      aria-label="Observed lap path comparison"
    >
      <header className="trajectory-comparison-heading">
        <div>
          <div className="eyebrow">OBSERVED WORLD-POSITION OVERLAY</div>
          <h3>Recorded paths in a shared frame</h3>
          <p>
            World X/Z coordinates · metres · equal scale · discontinuities preserved
          </p>
        </div>
        <span className="trajectory-badge">DIAGNOSTIC ONLY</span>
      </header>
      {probeForm}
      {positionProbe ? (
        <section className="trajectory-probe-evidence" aria-label="Position probe evidence">
          <header>
            <div>
              <div className="eyebrow">DISTANCE-ALIGNED POSITION PROBE</div>
              <h4>
                {positionProbe.requested_distance_m == null
                  ? "Requested distance unavailable"
                  : `${format(positionProbe.requested_distance_m)} m along lap`}
              </h4>
              <p>Observed world position or interpolation between adjacent source samples.</p>
            </div>
            <span className={`trajectory-probe-status is-${positionProbe.status}`}>
              {positionProbe.status}
            </span>
          </header>
          <div className="trajectory-probe-sides">
            {(["target", "reference"] as const).map((sideName) => (
              <ProbeEvidence
                key={sideName}
                label={sideName === "target" ? "Target" : "Reference"}
                probe={positionProbe[sideName]}
              />
            ))}
          </div>
          <div className="trajectory-probe-difference">
            {positionProbe.difference.status === "available" ? (
              <>
                <strong>Target minus reference</strong>
                <span>ΔX {format(positionProbe.difference.world_x_m)} m</span>
                <span>ΔZ {format(positionProbe.difference.world_z_m)} m</span>
                <span>Horizontal separation {format(positionProbe.difference.horizontal_separation_m)} m</span>
              </>
            ) : (
              <span>
                Paired position difference unavailable
                {positionProbe.difference.reason_code
                  ? ` · ${humanize(positionProbe.difference.reason_code)}`
                  : ""}
              </span>
            )}
          </div>
        </section>
      ) : null}
      <div className="trajectory-comparison-paths">
        {(["target", "reference"] as const).map((sideName) => {
          const path = report.paths[sideName];
          const evidence = path.attempt_evidence;
          const gameValidity =
            evidence.game_valid === true
              ? "valid"
              : evidence.game_valid === false
                ? "invalid"
                : "unknown";
          const captureCompletion =
            path.capture_evidence?.complete === true
              ? "complete"
              : path.capture_evidence?.complete === false
                ? "incomplete"
                : "unknown";
          const warnings = [
            !evidence.reference_eligible ? "not reference eligible" : null,
            ...evidence.lifecycle_exclusions.map((reason) =>
              reason.replaceAll("_", " "),
            ),
          ].filter((value): value is string => value !== null);
          return (
            <article key={sideName}>
              <div className={`trajectory-comparison-dot is-${sideName}`} />
              <div>
                <strong>{sideName === "target" ? "Target" : "Reference"}</strong>
                <small>{path.source.attempt_key}</small>
                <small>
                  {path.preview.source_position_point_count.toLocaleString()} source points ·{" "}
                  {path.preview.rendered_point_count.toLocaleString()} shown ·{" "}
                  {(path.coverage.position_sample_coverage * 100).toFixed(1)}% coverage
                </small>
                {path.capture_evidence ? (
                  <small>
                    Capture completion {captureCompletion}
                    {path.capture_evidence.footer_status
                      ? ` · footer ${path.capture_evidence.footer_status}`
                      : ""}
                  </small>
                ) : <small>Capture completion unknown</small>}
                <small>Game validity {gameValidity}</small>
                {warnings.length ? (
                  <small className="trajectory-comparison-warning">
                    {warnings.join(" · ")}
                  </small>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
      <div className="trajectory-comparison-plot-wrap">
        <svg
          className="trajectory-comparison-plot"
          viewBox={`0 0 ${side} ${side}`}
          role="img"
          aria-label="Target and reference observed paths plotted in world X and Z coordinates on the same metre scale"
        >
          <rect width={side} height={side} className="trajectory-comparison-background" />
          <text x="22" y="28" className="trajectory-comparison-axis-label">
            WORLD Z ↑
          </text>
          <text
            x={side - 22}
            y={side - 14}
            textAnchor="end"
            className="trajectory-comparison-axis-label"
          >
            WORLD X →
          </text>
          {(["target", "reference"] as const).flatMap((sideName) => {
            const color = sideName === "target" ? "#f0b45c" : "#71c7b5";
            return report.paths[sideName].segments.map((segment, index) => {
              const coordinates = segment.points.map((point) => ({
                x: x(point.world_position_m.x),
                y: y(point.world_position_m.z),
              }));
              if (coordinates.length === 1) {
                const point = coordinates[0];
                return (
                  <circle
                    key={`${sideName}-${index}`}
                    cx={point.x}
                    cy={point.y}
                    r="3.5"
                    fill={color}
                    aria-hidden="true"
                  />
                );
              }
              const path = coordinates
                .map((point, pointIndex) =>
                  `${pointIndex === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`,
                )
                .join(" ");
              return (
                <path
                  key={`${sideName}-${index}`}
                  d={path}
                  fill="none"
                  stroke={color}
                  strokeWidth="2.5"
                  strokeDasharray={sideName === "reference" ? "6 5" : undefined}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                  aria-hidden="true"
                />
              );
            });
          })}
          {positionProbe ? (["target", "reference"] as const).map((sideName) => {
            const probe = positionProbe[sideName];
            const position = probe.status === "available"
              ? probe.position_world_xyz_m
              : null;
            if (!position) return null;
            const cx = x(position.x);
            const cy = y(position.z);
            const color = sideName === "target" ? "#f0b45c" : "#71c7b5";
            return sideName === "target" ? (
              <circle
                key={`probe-${sideName}`}
                cx={cx}
                cy={cy}
                r="7"
                fill={color}
                stroke="#0a0e0f"
                strokeWidth="2"
                aria-hidden="true"
              />
            ) : (
              <path
                key={`probe-${sideName}`}
                d={`M${cx},${cy - 8} L${cx + 8},${cy} L${cx},${cy + 8} L${cx - 8},${cy} Z`}
                fill={color}
                stroke="#0a0e0f"
                strokeWidth="2"
                aria-hidden="true"
              />
            );
          }) : null}
        </svg>
      </div>
      <div className="trajectory-comparison-legend">
        <span><i className="is-target" />Target · circle</span>
        <span><i className="is-reference" />Reference · dashed</span>
        {positionProbe ? <span>Probe markers · target circle / reference diamond</span> : null}
        <span>Gaps are not connected</span>
      </div>
      <p className="trajectory-comparison-note">
        These are the cars’ recorded positions. The probe uses world coordinates; it does not report line offset or time loss.
      </p>
    </section>
  );
}

function ProbeEvidence({
  label,
  probe,
}: {
  label: string;
  probe: ObservedPositionProbe;
}) {
  const position = probe.position_world_xyz_m;
  return (
    <article className={`trajectory-probe-card is-${probe.status}`}>
      <h5>{label}</h5>
      {probe.status === "available" && position ? (
        <>
          <strong className="trajectory-probe-coordinates">
            X {format(position.x)} · Y {format(position.y)} · Z {format(position.z)} m
          </strong>
          <small>
            {probe.method === "exact_source_observation"
              ? "Exact source observation"
              : `Linear interpolation · ${(100 * (probe.interpolation_fraction ?? 0)).toFixed(1)}%`}
            {probe.segment_index == null ? "" : ` · segment ${probe.segment_index}`}
          </small>
          <div className="trajectory-probe-anchors">
            {probe.source_anchors.map((anchor) => (
              <small key={`${anchor.frame_identifier}-${anchor.session_time_s}`}>
                Frame {anchor.frame_identifier} · {format(anchor.session_time_s)} s · {format(anchor.lap_distance_m)} m
              </small>
            ))}
          </div>
          <small className="trajectory-probe-source">
            Source {probe.source?.attempt_key ?? "unknown"} · trace {probe.source?.trace_sha256?.slice(0, 12) ?? "unknown"}… · schema {probe.source?.trace_schema_version ?? "unknown"}
          </small>
        </>
      ) : (
        <>
          <strong>Unavailable</strong>
          <small>{probe.reason_code ? humanize(probe.reason_code) : "Source position evidence unavailable."}</small>
          <small className="trajectory-probe-source">
            Source {probe.source?.attempt_key ?? "unknown"} · trace {probe.source?.trace_sha256?.slice(0, 12) ?? "unknown"}… · schema {probe.source?.trace_schema_version ?? "unknown"}
          </small>
        </>
      )}
    </article>
  );
}

function format(value: number | null): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(2);
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
