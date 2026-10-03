import type {
  AttemptTraceChannelKey,
  AttemptTraceChannelPreview,
  AttemptTraceChartReport,
} from "@/lib/api";

const CHART_WIDTH = 960;
const CHART_HEIGHT = 230;
const PLOT_LEFT = 64;
const PLOT_RIGHT = CHART_WIDTH - 18;
const PLOT_TOP = 18;
const PLOT_BOTTOM = 178;
const TICK_COUNT = 4;

const CHANNEL_KEYS: AttemptTraceChannelKey[] = ["speed", "throttle", "brake", "steering"];

export default function AttemptTraceCharts({
  report,
  unavailableReason,
}: {
  report: AttemptTraceChartReport | null;
  unavailableReason: string | null;
}) {
  if (!report) {
    return (
      <section className="attempt-traces panel">
        <div className="attempt-traces-heading">
          <div>
            <span className="eyebrow">STORED TELEMETRY · SINGLE ATTEMPT</span>
            <h2>Player traces</h2>
          </div>
          <span className="trace-chart-badge">UNAVAILABLE</span>
        </div>
        <p className="quality-unavailable">
          {unavailableReason ? humanize(unavailableReason) : "The local API did not return chartable traces for this attempt."}
        </p>
      </section>
    );
  }

  const { source, context, channels, preview } = report;
  const modeLabel = context.game_modes.map(humanize).join(" · ");
  const sessionLabel = context.session_types.map(humanize).join(" · ");
  const trackLabel = context.track_names.map(humanize).join(" · ");

  return (
    <section className="attempt-traces panel" aria-labelledby="attempt-traces-title">
      <div className="attempt-traces-heading">
        <div>
          <span className="eyebrow">STORED TELEMETRY · SINGLE ATTEMPT</span>
          <h2 id="attempt-traces-title">Player traces</h2>
        </div>
        <span className="trace-chart-badge">DIAGNOSTIC ONLY</span>
      </div>
      <p className="attempt-traces-description">
        Recorded session time is the horizontal axis. Runs stay separate across missing values and continuity breaks. These charts describe this attempt and do not compare performance.
      </p>
      <div className="trace-chart-provenance" aria-label="Trace source details">
        <div><span>GAME MODE</span><strong>{modeLabel}</strong></div>
        <div><span>SESSION</span><strong>{sessionLabel}</strong></div>
        <div><span>TRACK</span><strong>{trackLabel}</strong></div>
        <div><span>ATTEMPT</span><strong>{humanize(source.disposition)}</strong></div>
        <div><span>GAME VALIDITY</span><strong>{source.game_valid === true ? "VALID" : source.game_valid === false ? "INVALID" : "UNKNOWN"}</strong></div>
        <div><span>TRACE SCHEMA</span><strong>V{source.trace_schema_version}</strong></div>
      </div>
      {source.exclusion_reasons.length > 0 ? (
        <p className="trace-chart-exclusions">
          Reference exclusions: {source.exclusion_reasons.map(humanize).join(" · ")}
        </p>
      ) : null}
      {report.status === "no_chartable_samples" ? (
        <p className="quality-unavailable">No samples have both valid chart anchors and finite source values for these channels.</p>
      ) : null}
      <div className="trace-chart-grid">
        {CHANNEL_KEYS.map((key) => (
          <TraceChannelChart key={key} channelKey={key} channel={channels[key]} />
        ))}
      </div>
      <p className="trace-chart-footnote">
        At most {preview.point_limit_per_channel.toLocaleString()} points and {preview.run_limit_per_channel.toLocaleString()} runs are rendered per channel. Frame, session-time and lap-distance anchors remain attached to every plotted point. The trace checksum was verified before analysis.
      </p>
    </section>
  );
}

function TraceChannelChart({
  channelKey,
  channel,
}: {
  channelKey: AttemptTraceChannelKey;
  channel: AttemptTraceChannelPreview;
}) {
  const allPoints = channel.segments.flatMap((segment) => segment.points);
  if (allPoints.length === 0) {
    return (
      <article className={`trace-channel trace-channel-${channelKey}`}>
        <div className="trace-channel-heading">
          <h3>{channel.label}</h3>
          <span>{channel.observed_sample_count.toLocaleString()} source samples</span>
        </div>
        <p className="trace-channel-empty">
          No finite {channel.source_field} values have valid frame and session-time anchors.
        </p>
      </article>
    );
  }

  let minimumTime = allPoints[0].session_time_s;
  let maximumTime = minimumTime;
  for (const point of allPoints) {
    minimumTime = Math.min(minimumTime, point.session_time_s);
    maximumTime = Math.max(maximumTime, point.session_time_s);
  }
  if (minimumTime === maximumTime) {
    minimumTime -= 0.5;
    maximumTime += 0.5;
  }

  const observedRange = channel.observed_value_range ?? [0, 1];
  const rawValueRange = observedRange[1] - observedRange[0];
  const valuePadding = rawValueRange > 0
    ? rawValueRange * 0.05
    : Math.max(Math.abs(observedRange[0]) * 0.05, 0.1);
  const minimumValue = observedRange[0] - valuePadding;
  const maximumValue = observedRange[1] + valuePadding;
  const valueSpan = maximumValue - minimumValue || 1;
  const timeSpan = maximumTime - minimumTime || 1;
  const xFor = (time: number) => PLOT_LEFT + ((time - minimumTime) / timeSpan) * (PLOT_RIGHT - PLOT_LEFT);
  const yFor = (value: number) => PLOT_BOTTOM - ((value - minimumValue) / valueSpan) * (PLOT_BOTTOM - PLOT_TOP);
  const ticks = Array.from({ length: TICK_COUNT + 1 }, (_, index) => {
    const fraction = index / TICK_COUNT;
    return {
      y: PLOT_TOP + fraction * (PLOT_BOTTOM - PLOT_TOP),
      value: maximumValue - fraction * valueSpan,
    };
  });

  return (
    <article className={`trace-channel trace-channel-${channelKey}`}>
      <div className="trace-channel-heading">
        <h3>{channel.label}</h3>
        <span>{channel.observed_sample_count.toLocaleString()} source · {channel.rendered_point_count.toLocaleString()} plotted · {channel.unit}</span>
      </div>
      <svg
        className="trace-channel-chart"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        role="img"
        aria-label={`${channel.label} over recorded session time, with ${channel.rendered_run_count} separate runs`}
        preserveAspectRatio="none"
      >
        <rect x="0" y="0" width={CHART_WIDTH} height={CHART_HEIGHT} className="trace-chart-background" />
        {ticks.map((tick, index) => (
          <g key={index}>
            <line x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={tick.y} y2={tick.y} className="trace-chart-gridline" />
            <text x={PLOT_LEFT - 8} y={tick.y + 4} textAnchor="end" className="trace-chart-axis-text">
              {formatValue(tick.value)}
            </text>
          </g>
        ))}
        <line x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={PLOT_BOTTOM} y2={PLOT_BOTTOM} className="trace-chart-axis" />
        <text x={PLOT_LEFT} y={CHART_HEIGHT - 12} textAnchor="start" className="trace-chart-axis-text">
          {minimumTime.toFixed(2)} s
        </text>
        <text x={PLOT_RIGHT} y={CHART_HEIGHT - 12} textAnchor="end" className="trace-chart-axis-text">
          {maximumTime.toFixed(2)} s
        </text>
        <text x={CHART_WIDTH / 2} y={CHART_HEIGHT - 12} textAnchor="middle" className="trace-chart-axis-label">
          SESSION TIME · S
        </text>
        {channel.segments.map((segment) => (
          <g
            key={`${channelKey}-${segment.run_index}`}
            role="group"
            aria-label={`Run ${segment.run_index + 1}, ${segment.source_sample_count} source samples${segment.break_before_reasons.length ? `, break before ${segment.break_before_reasons.map(humanize).join(", ")}` : ""}`}
          >
            {segment.points.length > 1 ? (
              <polyline
                className="trace-channel-line"
                points={segment.points.map((point) => `${xFor(point.session_time_s).toFixed(2)},${yFor(point.value).toFixed(2)}`).join(" ")}
                aria-hidden="true"
              />
            ) : (
              <circle
                className="trace-channel-point"
                cx={xFor(segment.points[0].session_time_s)}
                cy={yFor(segment.points[0].value)}
                r="3.5"
                aria-hidden="true"
              />
            )}
          </g>
        ))}
      </svg>
      <p className="trace-channel-summary">
        {channel.rendered_run_count} of {channel.source_run_count} source runs shown · {channel.omitted_point_count.toLocaleString()} observed samples omitted from this bounded preview
        {channel.missing_value_sample_count > 0 ? ` · ${channel.missing_value_sample_count} missing values` : ""}
        {channel.invalid_anchor_sample_count > 0 ? ` · ${channel.invalid_anchor_sample_count} invalid chart anchors` : ""}
      </p>
    </article>
  );
}

function formatValue(value: number): string {
  return Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(1);
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
