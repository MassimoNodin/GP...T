import type {
  AttemptRegionReport,
  AttemptRegionWindow,
  RegionEvent,
} from "@/lib/api";

export default function AttemptRegionsPanel({
  report,
  unavailableReason,
}: {
  report: AttemptRegionReport | null;
  unavailableReason: string | null;
}) {
  if (!report) {
    return (
      <section className="region-analysis panel attempt-region-unavailable">
        <div className="eyebrow">SINGLE ATTEMPT REGION OBSERVATIONS</div>
        <h2>Region observations unavailable</h2>
        <p>{humanize(unavailableReason ?? "no_report_returned")}</p>
      </section>
    );
  }

  const { source, model } = report;
  return (
    <section className="region-analysis">
      <header className="region-overview panel">
        <div>
          <div className="eyebrow">
            {humanize(model.validation_status)} DISTANCE REGIONS ·{" "}
            {report.regions.length} WINDOWS
          </div>
          <h2>{model.track_name} attempt observations</h2>
          <p>{model.provenance}</p>
        </div>
        <span className="region-state region-state-draft">DIAGNOSTIC ONLY</span>
      </header>
      <p className="region-caveat">
        Attempt {source.attempt_number} · {humanize(source.disposition)} ·{" "}
        {validity(source.game_valid)} · recorded {source.source_sample_count.toLocaleString()} samples.
        These draft distance windows are not validated corners. Metrics use
        only observed, supported telemetry; there is no comparison or coaching.
      </p>
      <div className="region-grid">
        {report.regions.map((region, index) => (
          <AttemptRegionCard key={region.identifier} region={region} index={index} />
        ))}
      </div>
      <details className="attempt-region-provenance panel">
        <summary>Source and analysis provenance</summary>
        <dl>
          <div>
            <dt>Trace SHA-256</dt>
            <dd>{source.trace_sha256}</dd>
          </div>
          <div>
            <dt>Trace schema</dt>
            <dd>v{source.trace_schema_version}</dd>
          </div>
          <div>
            <dt>Model revision</dt>
            <dd>
              {model.model_id} · rev {model.revision}
            </dd>
          </div>
          <div>
            <dt>Analysis support</dt>
            <dd>
              {report.config.grid_step_m} m grid · max sample gaps{" "}
              {report.config.max_bracket_time_s * 1000} ms /{" "}
              {report.config.max_bracket_distance_m} m
            </dd>
          </div>
        </dl>
      </details>
    </section>
  );
}

function AttemptRegionCard({
  region,
  index,
}: {
  region: AttemptRegionWindow;
  index: number;
}) {
  const [start, end] = region.analysis_window_m;
  const observations = region.observations;
  const brake = observations.braking;
  const steering = observations.turn_in_proxy;
  const throttle = observations.throttle_pickup["0.5"];
  const minimumSpeed = observations.minimum_speed;

  return (
    <article className="region-card panel">
      <header className="region-card-header">
        <div>
          <div className="region-index">
            WINDOW {String(index + 1).padStart(2, "0")} · {Math.round(start)}–
            {Math.round(end)} M
          </div>
          <h3>{region.label}</h3>
        </div>
        <span className="region-state region-state-draft">DRAFT WINDOW</span>
      </header>

      <div className="region-stat-grid">
        <RegionStat
          title="OBSERVED MINIMUM SPEED"
          value={speed(minimumSpeed.speed_kph)}
          detail={`${humanize(minimumSpeed.status)} · ${percent(minimumSpeed.supported_grid_coverage)} speed support`}
        />
        <RegionStat
          title="BRAKING ONSET"
          value={distance(brake.distance_m)}
          detail={`${humanize(brake.status)} · ${eventCount(brake)}`}
        />
        <RegionStat
          title="STEERING ONSET PROXY"
          value={distance(steering.distance_m)}
          detail={`${humanize(steering.status)} · ${percent(observations.event_channel_coverage.steering)} steering support`}
        />
        <RegionStat
          title="50% THROTTLE ONSET"
          value={distance(throttle.distance_m)}
          detail={`${humanize(throttle.status)} · ${percent(observations.event_channel_coverage.throttle)} throttle support`}
        />
      </div>

      <div className="attempt-region-events">
        <EventEvidence title="Brake" event={brake} />
        <EventEvidence title="Steering threshold" event={steering} />
        <EventEvidence title="50% throttle" event={throttle} />
      </div>

      <div className="region-exits">
        <div className="region-subhead">
          <strong>Observed exit speeds</strong>
          <span>Configured distances from the draft window anchor</span>
        </div>
        <div className="region-table-wrap">
          <table>
            <thead>
              <tr>
                <th>OFFSET</th>
                <th>SPEED</th>
                <th>SUPPORT</th>
              </tr>
            </thead>
            <tbody>
              {observations.exit_speeds.map((item) => (
                <tr key={item.offset_m}>
                  <td>+{item.offset_m} m</td>
                  <td>{speed(item.speed_kph)}</td>
                  <td>{humanize(item.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p className="region-apex-note">
        Driver apex: unavailable. Track-relative geometry is not validated.
      </p>
    </article>
  );
}

function EventEvidence({
  title,
  event,
}: {
  title: string;
  event: RegionEvent;
}) {
  return (
    <section className="region-event">
      <div className="region-subhead">
        <strong>{title} examples</strong>
        <span>
          {humanize(event.status)} · {eventCount(event)}
        </span>
      </div>
      {event.distance_m === null ? (
        <p>{humanize(event.reason ?? event.status)}</p>
      ) : (
        <p>
          Selected onset: {distance(event.distance_m)}
          {event.distance_bracket_m?.length === 2
            ? ` · bracket ${distance(event.distance_bracket_m[0])}–${distance(event.distance_bracket_m[1])}`
            : ""}
        </p>
      )}
      {event.events.length ? (
        <ul>
          {event.events.map((episode, index) => (
            <li key={`${episode.start_distance_m}:${index}`}>
              {distance(episode.start_distance_m)}–{distance(episode.end_distance_m)}
              {episode.left_censored ? " · left censored" : ""}
              {episode.right_censored ? " · right censored" : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {event.events_truncated ? (
        <small>Examples capped at 20; total count is {event.event_count}.</small>
      ) : null}
    </section>
  );
}

function RegionStat({
  title,
  value,
  detail,
}: {
  title: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="region-stat">
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function eventCount(event: RegionEvent): string {
  const count = event.event_count ?? event.events.length;
  return `${count} sustained ${count === 1 ? "event" : "events"}${event.events_truncated ? " · examples capped" : ""}`;
}

function validity(gameValid: boolean | null): string {
  if (gameValid === true) return "game valid";
  if (gameValid === false) return "game invalid";
  return "validity unknown";
}

function speed(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : `${value.toFixed(1)} km/h`;
}

function distance(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : `${value.toFixed(1)} m`;
}

function percent(value: number): string {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—";
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
