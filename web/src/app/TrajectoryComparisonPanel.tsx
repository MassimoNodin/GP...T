import type {
  ObservedTrajectoryComparisonPreview,
} from "@/lib/api";

export default function TrajectoryComparisonPanel({
  report,
  unavailableReason,
}: {
  report: ObservedTrajectoryComparisonPreview | null;
  unavailableReason: string | null;
}) {
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
      </section>
    );
  }

  const [minX, maxX] = report.plot_bounds_world_xz_m.world_x;
  const [minZ, maxZ] = report.plot_bounds_world_xz_m.world_z;
  const side = 800;
  const span = maxX - minX;
  const x = (value: number) => ((value - minX) / span) * side;
  const y = (value: number) => ((maxZ - value) / span) * side;

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
        </svg>
      </div>
      <div className="trajectory-comparison-legend">
        <span><i className="is-target" />Target</span>
        <span><i className="is-reference" />Reference · dashed</span>
        <span>Gaps are not connected</span>
      </div>
      <p className="trajectory-comparison-note">
        These are the cars’ recorded positions. The overlay does not identify an ideal
        racing line or establish a driving cause.
      </p>
    </section>
  );
}
