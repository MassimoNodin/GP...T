"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useAcquisitionFeed, useRuntimeFeed } from "@/features/live-status/hooks";
import { useRecentlyLive } from "@/features/live-status/recent-live";
import { isReachable, liveDataPill } from "@/features/live-status/map";
import { Drawer } from "@/features/ui-foundation/Drawer";
import { Card, PageHeader } from "@/features/ui-foundation/primitives";
import { InlineAlert } from "@/features/ui-foundation/states";
import { EventDetails } from "./EventDetails";
import { RadioHistory } from "./RadioHistory";
import { SessionContext } from "./SessionContext";
import { SystemStatus } from "./SystemStatus";
import type { DriveModel } from "./load";
import {
  buildRadioRows,
  fixtureRadioRows,
  followSelection,
  newestId,
  pendingQuestion,
  radioCounts,
  type RadioFilter,
  type RadioRow,
} from "./radio";
import { initialFeedState } from "./radio-feed";
import { buildDriveRows, driveActivity } from "./system-rows";
import { useRadioFeed } from "./useRadioFeed";
import { useReadAloud } from "./useReadAloud";
import { useMicrophone, useSpeechStatus } from "./useSystemSources";
import styles from "./DriveDiagnostics.module.css";

const NARROW_QUERY = "(max-width: 1023px)";
const PENDING_TICK_MS = 5000;

const noopSubscribe = () => () => {};

function subscribeNarrow(listener: () => void) {
  const query = window.matchMedia(NARROW_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

/** Below 1024px the details open in a Drawer from the row. Server render assumes wide. */
function useNarrow(): boolean {
  return useSyncExternalStore(
    subscribeNarrow,
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false,
  );
}

const eventRowId = (seq: number) => `event-${seq}`;

function seqOf(rowId: string | null): number | null {
  if (!rowId?.startsWith("event-")) return null;
  const value = Number(rowId.slice("event-".length));
  return Number.isSafeInteger(value) ? value : null;
}

export function DriveDiagnostics({ model }: { model: DriveModel }) {
  const router = useRouter();
  const narrow = useNarrow();

  // Start from the server-read state (identical SSR output), then follow the
  // backend while the tab is visible. null = the backend did not answer.
  const acquisition = useAcquisitionFeed(model.initial.acquisition) ?? null;
  const recentlyLive = useRecentlyLive(acquisition);
  const runtime = useRuntimeFeed(model.initial.runtime) ?? null;
  const speech = useSpeechStatus(model.speech);
  const microphone = useMicrophone();
  const readAloud = useReadAloud(model.interruption);
  const liveData = liveDataPill(acquisition);

  /* ------------------------------------------------------------ radio feed */

  const [initialFeed] = useState(() =>
    initialFeedState({
      status: model.radio.state,
      reason: model.radio.reason,
      events: model.radio.events,
      nextAfter: model.radio.nextAfter,
    }),
  );
  // The reference path has no feed to poll; it re-reads the page once the engine answers.
  const feed = useRadioFeed(initialFeed, !model.reference);

  const timeZone = useSyncExternalStore(
    noopSubscribe,
    () => Intl.DateTimeFormat().resolvedOptions().timeZone,
    () => model.timeZone,
  );
  const [now, setNow] = useState(model.generatedAt);
  const answering = pendingQuestion(feed.events, now) !== null;
  useEffect(() => {
    if (!answering) return;
    const timer = window.setInterval(() => setNow(Date.now()), PENDING_TICK_MS);
    return () => window.clearInterval(timer);
  }, [answering]);

  const handledArrival = useRef(feed.arrivalSeq);
  const { offer } = readAloud;
  useEffect(() => {
    if (handledArrival.current === feed.arrivalSeq) return;
    handledArrival.current = feed.arrivalSeq;
    setNow(Date.now());
    offer(feed.arrived);
  }, [feed.arrivalSeq, feed.arrived, offer]);

  const rows = useMemo<RadioRow[]>(
    () =>
      model.reference
        ? fixtureRadioRows(model.radio.fixture)
        : buildRadioRows(feed.events, { now, timeZone, playback: readAloud.playback }),
    [model.reference, model.radio.fixture, feed.events, now, timeZone, readAloud.playback],
  );
  const counts = useMemo(() => radioCounts(rows), [rows]);

  /* ------------------------------------- selection, follow mode and unread */

  const newest = newestId(feed.events);
  const [filter, setFilter] = useState<RadioFilter>("all");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [markedThrough, setMarkedThrough] = useState(() => newestId(model.radio.events));
  const [readSeqs, setReadSeqs] = useState<ReadonlySet<number>>(() => new Set());
  const [track, setTrack] = useState<{ newest: number; selected: string | null }>(() => ({
    newest,
    selected: model.reference ? (model.radio.fixture[0]?.id ?? null) : newest > 0 ? eventRowId(newest) : null,
  }));

  // Follow mode: when the newest row was selected and newer events arrive, the
  // selection moves with it; otherwise the selection stays where the user put it.
  if (track.newest !== newest) {
    const moved = followSelection(seqOf(track.selected), track.newest || null, newest || null);
    const selected = moved === null ? track.selected : eventRowId(moved);
    setTrack({ newest, selected });
    if (moved !== null && !readSeqs.has(moved)) setReadSeqs(new Set(readSeqs).add(moved));
  }

  const selectedRow = rows.find((row) => row.id === track.selected) ?? rows[0] ?? null;

  const unreadSeqs = useMemo(() => {
    const unread = new Set<number>();
    for (const event of feed.events) {
      if (event.id > markedThrough && !readSeqs.has(event.id) && eventRowId(event.id) !== selectedRow?.id) {
        unread.add(event.id);
      }
    }
    return unread;
  }, [feed.events, markedThrough, readSeqs, selectedRow?.id]);
  const unreadIds = useMemo(() => new Set([...unreadSeqs].map(eventRowId)), [unreadSeqs]);
  const freshIds = useMemo(() => new Set(feed.arrived.map((event) => eventRowId(event.id))), [feed.arrived]);

  function select(row: RadioRow) {
    setTrack({ newest: track.newest, selected: row.id });
    if (row.seq !== null && !readSeqs.has(row.seq)) setReadSeqs(new Set(readSeqs).add(row.seq));
    if (narrow) setDrawerOpen(true);
  }

  /* ------------------------------------------------------ system and context */

  const reachable = isReachable(acquisition, runtime);
  const systemRows = useMemo(
    () =>
      buildDriveRows({
        acquisition,
        recentlyLive,
        runtime,
        microphone,
        speech,
        audio: readAloud.audio,
        proactive: model.proactive,
      }),
    [acquisition, recentlyLive, runtime, microphone, speech, readAloud.audio, model.proactive],
  );
  const activity = driveActivity({ acquisition, recentlyLive, runtime, speaking: readAloud.speaking, answering });

  // On the reference path, re-read the page as soon as the engine answers.
  useEffect(() => {
    if (model.reference && reachable) router.refresh();
  }, [model.reference, reachable, router]);

  const announcement = feed.arrived
    .filter((event) => event.priority === "high")
    .map((event) => event.text)
    .join(" ");

  const readAloudNote =
    readAloud.audio.kind === "no_voice"
      ? "No local voice is installed, so calls cannot be read aloud. Install a system voice."
      : readAloud.audio.kind === "unsupported"
        ? "This browser cannot read calls aloud."
        : null;

  return (
    <>
      {model.reference && (
        <div className={styles.banner}>
          <InlineAlert tone="info" title="Reference data">
            The local engine is not reachable, so example sessions from the reference design are shown. Start the
            engine to see your own data.
          </InlineAlert>
        </div>
      )}
      <PageHeader title="Drive Diagnostics" subtitle="Monitor voice, telemetry and assistant systems in real time." />
      <div className={styles.topGrid}>
        <SystemStatus rows={systemRows} activity={activity} />
        <SessionContext context={model.context} photo={model.photo} live={liveData.live} />
      </div>

      <div className={styles.bottomGrid}>
        <RadioHistory
          rows={rows}
          counts={counts}
          filter={filter}
          onFilter={setFilter}
          selectedId={selectedRow?.id ?? null}
          onSelect={select}
          unread={unreadIds}
          fresh={freshIds}
          onMarkAllRead={() => setMarkedThrough(newest)}
          feed={{ status: feed.status, reference: model.reference }}
          readAloud={{ enabled: readAloud.enabled, onChange: readAloud.setEnabled, note: readAloudNote }}
        />

        {!narrow && (
          <Card title="Event Details" labelledBy="event-details" className={styles.detailCard}>
            {selectedRow ? (
              <EventDetails row={selectedRow} />
            ) : (
              <p className={styles.muted}>{rows.length === 0 ? "No radio events yet." : "Select a radio event."}</p>
            )}
          </Card>
        )}
      </div>

      {narrow && selectedRow && (
        <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title={selectedRow.title}>
          <EventDetails row={selectedRow} />
        </Drawer>
      )}

      <div className="sr-only" role="status" aria-live="polite">
        {announcement}
      </div>
    </>
  );
}
