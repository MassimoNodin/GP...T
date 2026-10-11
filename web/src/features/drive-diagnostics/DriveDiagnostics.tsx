"use client";

import { useMemo, useState } from "react";
import { useAcquisitionFeed, useRuntimeFeed } from "@/features/live-status/hooks";
import { buildSystemRows, currentActivity, liveDataPill } from "@/features/live-status/map";
import type { RadioEvent, RadioKind } from "@/features/review-data/fixtures";
import { InfoIcon, StatusGlyph, WaveformIcon } from "@/features/ui-foundation/icons";
import { Card, PageHeader, StatusBadge } from "@/features/ui-foundation/primitives";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import type { DriveModel } from "./load";
import styles from "./DriveDiagnostics.module.css";

type Filter = "all" | RadioKind;

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "all", label: "All" },
  { key: "question", label: "Questions" },
  { key: "response", label: "Responses" },
  { key: "proactive", label: "Proactive" },
  { key: "issue", label: "Issues" },
];

const KIND: Record<RadioKind, { label: string; title: string }> = {
  proactive: { label: "Proactive", title: "Proactive Message" },
  question: { label: "You", title: "Driver Question" },
  response: { label: "Engineer", title: "Engineer Response" },
  issue: { label: "Issue", title: "Issue" },
};

function glyphFor(tone: "green" | "amber" | "red" | "grey") {
  return tone === "green" ? "check" : tone === "grey" ? "dot" : "alert";
}

function KindIcon({ kind }: { kind: RadioKind }) {
  if (kind === "proactive") return <StatusGlyph tone="green" glyph="square" size={13} />;
  if (kind === "question") return <StatusGlyph tone="blue" glyph="user" size={13} />;
  if (kind === "response") return <StatusGlyph tone="dark" glyph="flag" size={13} />;
  return <StatusGlyph tone="red" glyph="alert" size={13} />;
}

export function DriveDiagnostics({ model }: { model: DriveModel }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState(model.radio[0]?.id ?? "");
  // Start from the server-read state (identical SSR output), then follow the
  // backend while the tab is visible. null = the backend did not answer.
  const acquisition = useAcquisitionFeed(model.initial.acquisition) ?? null;
  const runtime = useRuntimeFeed(model.initial.runtime) ?? null;
  const system = useMemo(
    () => buildSystemRows(model.referenceSystem, acquisition, runtime),
    [model.referenceSystem, acquisition, runtime],
  );
  const activity = currentActivity(model.activity, acquisition, runtime);
  const liveData = liveDataPill(acquisition);

  const counts = useMemo(() => {
    const result: Record<Filter, number> = { all: model.radio.length, question: 0, response: 0, proactive: 0, issue: 0 };
    for (const event of model.radio) result[event.kind] += 1;
    return result;
  }, [model.radio]);

  const visible = filter === "all" ? model.radio : model.radio.filter((event) => event.kind === filter);
  const selected = model.radio.find((event) => event.id === selectedId) ?? visible[0] ?? null;

  return (
    <>
      <PageHeader title="Drive Diagnostics" subtitle="Monitor voice, telemetry and assistant systems in real time." />
      <div className={styles.topGrid}>
        <Card title="System Status" labelledBy="system-status" className={styles.systemCard}>
          <ul className={styles.systemList}>
            {system.map((row) => (
              <li key={row.label} className={styles.systemRow}>
                <StatusGlyph tone={row.tone} glyph={glyphFor(row.tone)} size={14} />
                <span className={styles.systemLabel}>{row.label}</span>
                <span className={styles.systemStatus} data-tone={row.tone}>
                  {row.status}
                </span>
                <span className={styles.systemDetail}>{row.detail}</span>
              </li>
            ))}
            <li className={styles.systemRow}>
              <StatusGlyph tone={activity.tone} glyph={glyphFor(activity.tone)} size={14} />
              <span className={styles.systemLabel}>Current Activity</span>
              <span className={styles.activity}>
                <span>{activity.text}</span>
                {activity.tone === "green" && <WaveformIcon size={40} />}
              </span>
            </li>
          </ul>
        </Card>

        <Card title="Session Context" labelledBy="session-context" className={styles.contextCard}>
          <div className={styles.contextHead}>
            <div>
              <div className={styles.contextTitle}>{model.context.title}</div>
              <div className={styles.contextSub}>
                {model.context.subtitle.map((part, index) => (
                  <span key={part}>
                    {index > 0 && <span className={styles.dot}>•</span>}
                    {part}
                  </span>
                ))}
              </div>
            </div>
            <TrackMap track={model.context.track} className={styles.contextMap} strokeWidth={2.4} />
          </div>
          <div className={styles.contextBody}>
            <TrackPhoto src={model.photo} alt={`${model.context.title} circuit`} className={styles.contextPhoto} />
            <dl className={styles.facts}>
              {model.context.facts.map(([label, value]) => (
                <div key={label} className={styles.fact}>
                  <dt>{label}:</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </div>
          <div className={styles.liveWrap}>
            <span className={styles.livePill} data-live={liveData.live}>
              <StatusGlyph tone={liveData.live ? "red" : "grey"} glyph="dot" size={14} />
              {liveData.label}
            </span>
          </div>
        </Card>
      </div>

      <div className={styles.bottomGrid}>
        <Card title="Radio History" labelledBy="radio-history" className={styles.radioCard}>
          <div className={styles.filters} role="group" aria-label="Filter radio history">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                aria-pressed={filter === item.key}
                className={styles.filter}
                onClick={() => setFilter(item.key)}
              >
                {item.label} ({counts[item.key]})
              </button>
            ))}
          </div>
          <ul className={styles.radioList} aria-label="Radio events">
            {visible.map((event) => (
              <li key={event.id}>
                <button
                  type="button"
                  className={styles.radioRow}
                  aria-pressed={selected?.id === event.id}
                  onClick={() => setSelectedId(event.id)}
                >
                  <span className={styles.radioTime}>{event.time}</span>
                  <span className={styles.radioKind} data-kind={event.kind}>
                    <KindIcon kind={event.kind} />
                    {KIND[event.kind].label}
                  </span>
                  <span className={styles.radioText}>{event.text}</span>
                  <span className={styles.radioOutcome}>{event.outcome}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>

        <Card title="Event Details" labelledBy="event-details" className={styles.detailCard}>
          {selected ? <EventDetails event={selected} /> : <p className={styles.muted}>Select a radio event.</p>}
        </Card>
      </div>
    </>
  );
}

function EventDetails({ event }: { event: RadioEvent }) {
  const sections: Array<[string, string | string[]]> = [
    ["Message", event.text],
    ["Trigger", event.trigger],
    ["Evidence / Context", event.evidence],
    ["Output", event.output],
    ["Timings", event.timings],
  ];
  return (
    <div aria-live="polite">
      <div className={styles.detailHead}>
        <StatusGlyph tone={event.kind === "issue" ? "red" : event.kind === "question" ? "blue" : "green"} glyph="check" size={14} />
        <div>
          <div className={styles.detailTitle}>{KIND[event.kind].title}</div>
          <div className={styles.detailTime}>{event.timestamp}</div>
        </div>
        <span className={styles.outcomeBadge} data-tone={event.outcomeTone}>
          {event.outcome}
        </span>
      </div>
      {sections.map(([label, body]) => (
        <div key={label} className={styles.detailSection}>
          <div className={styles.detailLabel}>{label}</div>
          {Array.isArray(body) ? (
            <ul className={styles.bullets}>
              {body.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : (
            <p className={styles.detailBody}>{body}</p>
          )}
        </div>
      ))}
      {event.limitations && (
        <div className={styles.detailSection}>
          <div className={styles.detailLabel}>
            Evidence Limitations <InfoIcon size={13} />
          </div>
          <p className={styles.detailBody}>{event.limitations}</p>
        </div>
      )}
    </div>
  );
}
