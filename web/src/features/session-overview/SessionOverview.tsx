"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { NotableEvent } from "@/features/review-data/fixtures";
import { formatDelta, formatLapTime } from "@/features/review-data/format";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  MoreIcon,
  OpenDocumentIcon,
  StatusGlyph,
} from "@/features/ui-foundation/icons";
import { Card, IconButton, Menu, Select, StatusBadge, Tabs } from "@/features/ui-foundation/primitives";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import { LapTimeChart } from "./LapTimeChart";
import type { OverviewLap, OverviewModel } from "./load";
import styles from "./SessionOverview.module.css";

type View = "overview" | "laps" | "stints" | "events" | "setup";
type Mode = "single" | "compare";

const VIEWS: Array<{ key: View; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "laps", label: "Laps" },
  { key: "stints", label: "Stints" },
  { key: "events", label: "Events" },
  { key: "setup", label: "Setup & Conditions" },
];

export function SessionOverview({ model }: { model: OverviewModel }) {
  const [view, setView] = useState<View>("overview");
  const [mode, setMode] = useState<Mode>("single");
  const [selectedId, setSelectedId] = useState(model.selectedId);

  const index = Math.max(0, model.laps.findIndex((lap) => lap.id === selectedId));
  const selected = model.laps[index];
  const best = model.laps.find((lap) => lap.id === model.bestId) ?? null;

  const analysisHref = useMemo(() => {
    const query = new URLSearchParams();
    if (selected) query.set("target", selected.id);
    if (best) query.set("reference", best.id);
    const encoded = query.toString();
    return `/review/${encodeURIComponent(model.sessionKey)}/laps${encoded ? `?${encoded}` : ""}`;
  }, [best, model.sessionKey, selected]);

  return (
    <>
      <div className={styles.toolbar}>
        <Tabs items={VIEWS} value={view} onChange={setView} label="Session sections" />
        <div className={styles.toolbarActions}>
          <Link href={analysisHref} className={styles.openButton}>
            <OpenDocumentIcon size={15} />
            Open in Lap Analysis
          </Link>
          <Menu
            label="More session actions"
            trigger={<MoreIcon size={16} />}
            triggerClassName={styles.moreButton}
            panelClassName={styles.morePanel}
          >
            <Link href={analysisHref}>Open in Lap Analysis</Link>
            <Link href="/review">Back to Session Library</Link>
          </Menu>
        </div>
      </div>

      {(view === "overview" || view === "setup") && <Hero model={model} />}

      {view === "overview" && (
        <div className={styles.grid}>
          <div className={styles.col}>
            <Card
              title="Lap Time Progression"
              labelledBy="lap-progression"
              className={styles.progressionCard}
            >
              <div className={styles.legend}>
                <span className={styles.showLabel}>Show:</span>
                <Select aria-label="Chart metric" defaultValue="lap" className={styles.metric}>
                  <option value="lap">Lap Time</option>
                </Select>
                <span className={styles.key}>
                  <i style={{ background: "#22a36b" }} />
                  Personal Best
                </span>
                <span className={styles.key}>
                  <i style={{ background: "#9b34ef" }} />
                  Session Best
                </span>
                <span className={styles.key}>
                  <i style={{ background: "#2563eb" }} />
                  Selected Lap
                </span>
              </div>
              {selected ? (
                <LapTimeChart
                  {...model.progression}
                  selected={{ lap: selected.lap, ms: selected.ms }}
                  bestMs={best?.ms ?? null}
                />
              ) : (
                <Unavailable>No timed laps were recorded for this session.</Unavailable>
              )}
              {model.lapDataNote && <p className={styles.dataNote}>{model.lapDataNote}</p>}
            </Card>
            <StintCard stints={model.stints} />
          </div>
          <div className={styles.col}>
            <Card title="Session Summary" labelledBy="session-summary" className={styles.summaryCard}>
              {model.summary ? (
                <>
                  <p className={styles.summaryText}>{model.summary.text}</p>
                  {model.summary.items.length > 0 && (
                    <ul className={styles.summaryList}>
                      {model.summary.items.map((item) => (
                        <li key={item.text}>
                          <StatusGlyph tone={item.tone} glyph={item.tone === "green" ? "check" : "alert"} size={15} />
                          {item.text}
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : (
                <Unavailable>A summary is not available for this session.</Unavailable>
              )}
            </Card>
            <EventsCard events={model.events} compact />
          </div>
        </div>
      )}

      {view === "laps" && <LapsTable laps={model.laps} bestId={model.bestId} selectedId={selected?.id} onSelect={setSelectedId} />}
      {view === "stints" && <StintCard stints={model.stints} />}
      {view === "events" && <EventsCard events={model.events} />}

      <Card className={styles.selectionCard}>
        <h2 className={styles.selectionTitle}>Lap Selection</h2>
        <div className={styles.selectionBody}>
          <Tabs
            items={[
              { key: "single", label: "Single Lap" },
              { key: "compare", label: "Compare Laps" },
            ]}
            value={mode}
            onChange={setMode}
            label="Lap selection mode"
            className={styles.modeTabs}
          />
          {selected && (
            <div className={styles.stepper}>
              <IconButton
                label="Previous lap"
                className={styles.stepButton}
                disabled={index === 0}
                onClick={() => setSelectedId(model.laps[index - 1].id)}
              >
                <ChevronLeftIcon size={15} />
              </IconButton>
              <div className={styles.stepValue} aria-live="polite">
                <span className={styles.stepLap}>Lap {selected.lap}</span>
                <span className={styles.stepTime}>{formatLapTime(selected.ms)}</span>
                {best && <span className={styles.stepDelta}>{formatDelta(selected.ms - best.ms)}</span>}
              </div>
              <IconButton
                label="Next lap"
                className={styles.stepButton}
                disabled={index >= model.laps.length - 1}
                onClick={() => setSelectedId(model.laps[index + 1].id)}
              >
                <ChevronRightIcon size={15} />
              </IconButton>
              {mode === "compare" && best && (
                <span className={styles.compareWith}>
                  vs <strong>Lap {best.lap}</strong> {formatLapTime(best.ms)}
                </span>
              )}
            </div>
          )}
          <div className={styles.bestNote}>
            <StatusGlyph tone="blue" glyph="info" size={24} />
            <div>
              <div className={styles.bestTitle}>
                {best ? `Session best: ${formatLapTime(best.ms)} (Lap ${best.lap})` : "No eligible session best lap"}
              </div>
              <div className={styles.bestBody}>
                Comparisons are only available for laps with complete telemetry data.
              </div>
            </div>
          </div>
        </div>
      </Card>
    </>
  );
}

function Hero({ model }: { model: OverviewModel }) {
  const { hero } = model;
  return (
    <Card className={styles.hero}>
      <div className={styles.heroTop}>
        <div className={styles.heroIdentity}>
          <TrackPhoto src={model.photo} alt={`${hero.title} circuit`} className={styles.heroPhoto} />
          <div className={styles.heroText}>
            <h1 className={styles.heroTitle}>{hero.title}</h1>
            {hero.lines.map((line) => (
              <div key={line.join("-")} className={styles.heroLine}>
                {line.map((part, index) => (
                  <span key={`${part}-${index}`}>
                    {index > 0 && <span className={styles.dot}>•</span>}
                    {part}
                  </span>
                ))}
              </div>
            ))}
          </div>
          <TrackMap track={hero.track} className={styles.heroMap} strokeWidth={2.2} />
        </div>
        <div className={styles.heroStatsWrap}>
          <div className={styles.heroStatus}>
            <StatusBadge tone={hero.status.tone} chevron>
              {hero.status.label}
            </StatusBadge>
          </div>
          <dl className={styles.heroStats}>
            {hero.stats.map(([value, label]) => (
              <div key={label}>
                <dd>{value}</dd>
                <dt>{label}</dt>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <dl className={styles.heroDetails}>
        {hero.details.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

const STINT_COLORS = { Soft: "var(--stint-soft)", Medium: "var(--stint-medium)", Hard: "var(--stint-hard)" };

function Unavailable({ children }: { children: React.ReactNode }) {
  return <p className={styles.unavailable}>{children}</p>;
}

function StintCard({ stints }: { stints: OverviewModel["stints"] }) {
  if (!stints || stints.length === 0) {
    return (
      <Card title="Stint Structure" labelledBy="stint-structure" className={styles.stintCard}>
        <Unavailable>Tyre stint data is not available for this session.</Unavailable>
      </Card>
    );
  }
  const total = stints.reduce((sum, stint) => sum + stint.laps, 0);
  const ticks = [1, ...Array.from({ length: Math.floor(total / 10) }, (_, i) => (i + 1) * 10).filter((t) => t < total - 4), total];
  return (
    <Card title="Stint Structure" labelledBy="stint-structure" className={styles.stintCard}>
      <div className={styles.stintLegend}>
        {stints.map((stint) => (
          <span key={stint.compound} className={styles.key}>
            <i style={{ background: STINT_COLORS[stint.compound], borderRadius: "50%" }} />
            {stint.compound} ({stint.laps})
          </span>
        ))}
      </div>
      <div className={styles.stintTrack}>
        <div className={styles.stintGrid} aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick} style={{ left: `${((tick - 1) / (total - 1)) * 100}%` }} />
          ))}
        </div>
        <div
          className={styles.stintBar}
          role="img"
          aria-label={stints.map((stint) => `${stint.compound} ${stint.laps} laps`).join(", ")}
        >
          {stints.map((stint) => (
            <span
              key={stint.compound}
              style={{ flexGrow: stint.laps, background: STINT_COLORS[stint.compound] }}
            />
          ))}
        </div>
        <div className={styles.stintAxis} aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick} style={{ left: `${((tick - 1) / (total - 1)) * 100}%` }}>
              {tick}
            </span>
          ))}
        </div>
      </div>
    </Card>
  );
}

function EventIcon({ icon }: { icon: NotableEvent["icon"] }) {
  if (icon === "ring-green") return <StatusGlyph tone="green" glyph="ring" size={14} />;
  if (icon === "ring-amber") return <StatusGlyph tone="amber" glyph="ring" size={14} />;
  if (icon === "alert-red") return <StatusGlyph tone="red" glyph="alert" size={14} />;
  return <StatusGlyph tone="green" glyph="check" size={14} />;
}

function EventsCard({ events, compact }: { events: OverviewModel["events"]; compact?: boolean }) {
  if (!events || events.items.length === 0) {
    return (
      <Card
        title={`Notable Events (${events?.total ?? 0})`}
        labelledBy="notable-events"
        className={compact ? styles.eventsCard : undefined}
      >
        <Unavailable>
          {events ? "No notable events were derived from this session." : "Event data is not available for this session."}
        </Unavailable>
      </Card>
    );
  }
  return (
    <Card
      title={`Notable Events (${events.total})`}
      labelledBy="notable-events"
      className={compact ? styles.eventsCard : undefined}
    >
      <ul className={styles.events} data-compact={compact ? "true" : undefined}>
        {events.items.map((event) => (
          <li key={`${event.lap}-${event.text}`}>
            <EventIcon icon={event.icon} />
            <span className={styles.eventLap}>Lap {event.lap}</span>
            <span className={styles.eventText} data-highlight={event.highlight ? "true" : undefined}>
              {event.text}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function LapsTable({
  laps,
  bestId,
  selectedId,
  onSelect,
}: {
  laps: OverviewLap[];
  bestId: string | null;
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  const best = laps.find((lap) => lap.id === bestId);
  return (
    <Card title={`Laps (${laps.length})`} labelledBy="laps-table" className={styles.lapsCard}>
      <table className={styles.lapsTable}>
        <thead>
          <tr>
            <th scope="col">Lap</th>
            <th scope="col">Time</th>
            <th scope="col">Delta to best</th>
            <th scope="col">Valid</th>
          </tr>
        </thead>
        <tbody>
          {laps.map((lap) => (
            <tr key={lap.id} aria-selected={lap.id === selectedId} onClick={() => onSelect(lap.id)}>
              <td>
                <button type="button" onClick={() => onSelect(lap.id)}>
                  Lap {lap.lap}
                </button>
              </td>
              <td className="tabular">{formatLapTime(lap.ms)}</td>
              <td className="tabular" data-best={lap.id === bestId ? "true" : undefined}>
                {lap.id === bestId ? "Session best" : best ? formatDelta(lap.ms - best.ms) : "—"}
              </td>
              <td>{lap.valid ? "Yes" : "Invalid"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
