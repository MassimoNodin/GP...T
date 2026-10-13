"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { FilterChips } from "@/features/ui-foundation/FilterChips";
import { Card, Checkbox } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert, NotAvailable } from "@/features/ui-foundation/states";
import { Timeline, type TimelineItem } from "@/features/ui-foundation/Timeline";
import {
  analysisHref,
  EVENT_FILTERS,
  eventCounts,
  eventOrigin,
  eventsFooter,
  filterEvents,
  relativeEventTime,
  scanStoppedText,
  type EventFilter,
} from "./overview-data";
import { EventGlyph } from "./shared";
import type { ViewData } from "./view-types";
import viewStyles from "./OverviewViews.module.css";

export function EventsView({ data }: { data: ViewData }) {
  const { model, events, summary, laps, best } = data;
  const [filter, setFilter] = useState<EventFilter>("all");
  const [onlyMine, setOnlyMine] = useState(false);
  const counts = useMemo(() => eventCounts(events), [events]);
  const visible = useMemo(() => filterEvents(events, filter, onlyMine), [events, filter, onlyMine]);
  const origin = useMemo(() => eventOrigin(events), [events]);
  const loading = model.source === "v2" && summary.status === "loading";
  const loadedSummary = summary.status === "loaded" ? summary.value : null;

  // The time column appears only when some event has a session time; the rest
  // then say why theirs is missing instead of leaving the cell blank.
  const anyTime = visible.some((event) => relativeEventTime(event, origin) !== null);
  const items: TimelineItem[] = visible.map((event) => {
    const lap = event.isPlayer && event.lap !== null ? laps.find((candidate) => candidate.lap === event.lap) : undefined;
    return {
      id: event.id,
      icon: <EventGlyph icon={event.icon} />,
      lap: event.lap,
      time:
        relativeEventTime(event, origin) ??
        (anyTime ? <NotAvailable reason="The game did not report a session time for this event" compact /> : null),
      label: event.label,
      emphasis: event.highlight ? "highlight" : undefined,
      action: lap ? (
        <Link href={analysisHref(model.sessionKey, lap.id, best?.id ?? null)} className={viewStyles.viewLap}>
          View lap
        </Link>
      ) : undefined,
    };
  });

  if (loading) {
    return (
      <div className={viewStyles.stack}>
        <Card title="Events" labelledBy="events-card">
          <div className={viewStyles.boneStack} aria-hidden="true">
            {Array.from({ length: 6 }, (_, index) => (
              <Bone key={index} height={22} />
            ))}
          </div>
        </Card>
      </div>
    );
  }

  const filterLabel = EVENT_FILTERS.find((entry) => entry.key === filter)?.label.toLowerCase() ?? "";
  return (
    <div className={viewStyles.stack}>
      <Card title={`Events (${events.length})`} labelledBy="events-card">
        {events.length === 0 ? (
          <EmptyState title="No notable events were found in this session." />
        ) : (
          <>
            {loadedSummary && !loadedSummary.scan.complete && (
              <div className={viewStyles.alertGap}>
                <InlineAlert tone="warning">{scanStoppedText(loadedSummary.scan.stopReason)}</InlineAlert>
              </div>
            )}
            {model.source === "v2" && summary.status === "unavailable" && (
              <div className={viewStyles.alertGap}>
                <InlineAlert tone="info">
                  The session timeline is not available, so only events derived from your laps are shown.
                </InlineAlert>
              </div>
            )}
            <div className={viewStyles.tools}>
              <FilterChips
                label="Filter events"
                value={filter}
                onChange={setFilter}
                items={EVENT_FILTERS.map((entry) => ({ ...entry, count: counts[entry.key] }))}
              />
              <Checkbox label="Only events involving me" checked={onlyMine} onChange={setOnlyMine} />
            </div>
            <Timeline
              label="Session events"
              items={items}
              empty={
                <EmptyState
                  size="inline"
                  title={filter === "all" ? "No events involve you." : `No ${filterLabel} events.`}
                />
              }
            />
            <p className={viewStyles.eventsFooter}>{eventsFooter(events.length, loadedSummary)}</p>
          </>
        )}
      </Card>
    </div>
  );
}
