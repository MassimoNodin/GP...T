"use client";

import type { ReactNode } from "react";
import { useMemo } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { WeatherIcon } from "@/features/ui-foundation/icons";
import { Card } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert, NotAvailable } from "@/features/ui-foundation/states";
import { Timeline, type TimelineItem } from "@/features/ui-foundation/Timeline";
import { formatWeather } from "@/features/review-data/format";
import { UnitText } from "@/features/ui-foundation/units";
import {
  formatClock,
  sessionFacts,
  temperaturesText,
  weatherKind,
  weatherRows,
  type FactRow,
} from "./overview-data";
import type { ViewData } from "./view-types";
import styles from "./SessionOverview.module.css";
import viewStyles from "./OverviewViews.module.css";

export function SetupView({ data }: { data: ViewData }) {
  const { model, summary } = data;
  const loaded = summary.status === "loaded" ? summary.value : null;
  const loading = summary.status === "loading";
  const facts = useMemo(() => sessionFacts(model.context, loaded), [model.context, loaded]);
  return (
    <div className={`${styles.grid}`}>
      <div className={styles.col}>
        <ConditionsCard data={data} />
        <SetupCard data={data} />
      </div>
      <div className={styles.col}>
        <Card title="Session" labelledBy="session-facts">
          <dl className={viewStyles.facts}>
            {facts.map((row) => (
              <div key={row.label}>
                <dt>{row.label}</dt>
                <dd>
                  <FactValue row={row} loading={loading} />
                </dd>
              </div>
            ))}
          </dl>
        </Card>
        <AssistsCard data={data} />
      </div>
    </div>
  );
}

function FactValue({ row, loading }: { row: FactRow; loading: boolean }) {
  if (row.needsSummary && loading) return <Bone width={90} height={12} />;
  if (row.value === null) return <NotAvailable reason={row.reason ?? "Not reported"} />;
  return typeof row.value === "string" ? <UnitText>{row.value}</UnitText> : <>{row.value}</>;
}

function ConditionsCard({ data }: { data: ViewData }) {
  const { model, summary } = data;
  const loaded = summary.status === "loaded" ? summary.value : null;
  const { context } = model;
  const frame = (children: ReactNode) => (
    <Card title="Conditions" labelledBy="conditions">
      {children}
    </Card>
  );
  if (summary.status === "loading") return frame(<div className={viewStyles.boneStack} aria-hidden="true"><Bone height={14} /><Bone width="70%" height={12} /><Bone width="55%" height={12} /></div>);

  const timeline = loaded ? weatherRows(loaded.weather) : { rows: [], total: 0 };
  const origin = timeline.rows.map((row) => row.timeS).find((time): time is number => time !== null) ?? null;
  let rows = timeline.rows;
  if (rows.length === 0 && context.weather) {
    // No timeline: one row from the session's own context.
    rows = [
      {
        id: "context",
        timeS: null,
        kind: weatherKind(context.weather),
        label: formatWeather(context.weather) ?? "Weather",
        detail: temperaturesText(context.airTemperatureC, context.trackTemperatureC),
      },
    ];
  }
  if (rows.length === 0) return frame(<EmptyState size="inline" title="Weather was not reported for this session." />);

  const last = rows[rows.length - 1];
  const ongoing = loaded?.lifecycle === "active" || loaded?.lifecycle === "live";
  const items: TimelineItem[] = rows.map((row) => ({
    id: row.id,
    icon: <WeatherIcon kind={row.kind} size={16} />,
    lap: null,
    time: row.timeS !== null && origin !== null ? formatClock(row.timeS - origin) : null,
    label: row.label,
    chips: row.detail ? (
      <span>
        <UnitText>{row.detail}</UnitText>
      </span>
    ) : undefined,
  }));
  return frame(
    <>
      <div className={viewStyles.conditionNow}>
        <span className={viewStyles.conditionLabel}>{rows.length > 1 ? (ongoing ? "Now" : "End") : "Reported"}</span>
        <WeatherIcon kind={last.kind} size={18} />
        {last.label}
        {last.detail && <span className={viewStyles.conditionLabel}>{last.detail}</span>}
      </div>
      {rows.length > 1 && <Timeline label="Weather timeline" items={items} dense />}
      {timeline.total > rows.length && (
        <p className={viewStyles.note}>
          Showing the first {rows.length} of {timeline.total} weather changes.
        </p>
      )}
    </>,
  );
}

function SetupCard({ data }: { data: ViewData }) {
  const { model } = data;
  const { setup } = model;
  const title = model.source === "fixture" || setup ? "Car Setup (at session start)" : "Car Setup";
  if (!setup) {
    return (
      <Card title={title} labelledBy="car-setup">
        {model.source === "v2" ? (
          <EmptyState
            title="Car setup is not recorded for live sessions yet"
            body="Setup values are available for imported captures."
          />
        ) : (
          <EmptyState title="Car setup was not reported for this session." />
        )}
      </Card>
    );
  }
  return (
    <Card title={title} labelledBy="car-setup">
      {setup.changeCount > 0 && (
        <div className={viewStyles.alertGap}>
          <InlineAlert tone="info">
            The setup changed {setup.changeCount} {setup.changeCount === 1 ? "time" : "times"} during the session;
            values shown are from the start.
          </InlineAlert>
        </div>
      )}
      <div className={viewStyles.setupGroups}>
        {setup.groups.map((group) => (
          <section key={group.title} className={viewStyles.setupGroup}>
            <h3>{group.title}</h3>
            <dl className={viewStyles.facts}>
              {group.rows.map((row) => (
                <div key={row.label}>
                  <dt>{row.label}</dt>
                  <dd>
                    {row.value === null ? <NotAvailable reason="The game did not report this value" /> : row.value}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Card>
  );
}

function AssistsCard({ data }: { data: ViewData }) {
  // Only the reference session carries Assists and Damage values.
  const fields = data.model.hero.details.filter(
    (field) => (field.label === "Assists" || field.label === "Damage") && field.value !== null,
  );
  return (
    <Card title="Assists & Damage" labelledBy="assists-damage">
      {fields.length > 0 ? (
        <dl className={viewStyles.facts}>
          {fields.map((field) => (
            <div key={field.label}>
              <dt>{field.label}</dt>
              <dd>{field.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <EmptyState size="inline" title="Assist and damage settings are not included in the game's telemetry." />
      )}
    </Card>
  );
}
