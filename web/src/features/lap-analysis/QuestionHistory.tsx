"use client";

import { useEffect, useState } from "react";
import { Bone } from "@/features/route-states/Skeleton";
import { parseEngineerEventPage, type EngineerEvent } from "@/features/review-data/engineer-events";
import { humanReason } from "@/features/review-data/reasons";
import { Button } from "@/features/ui-foundation/primitives";
import { Drawer } from "@/features/ui-foundation/Drawer";
import { EmptyState, InlineAlert } from "@/features/ui-foundation/states";
import styles from "./Questions.module.css";

type Load =
  | { state: "loading" }
  | { state: "ready"; events: EngineerEvent[] }
  | { state: "failed"; message: string };

function when(iso: string): string {
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return "";
  return date.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** The engineer log of this session: questions asked (newest first) and the response each got. */
export function QuestionHistory({
  open,
  onClose,
  sessionKey,
  onAskAgain,
  canAsk,
}: {
  open: boolean;
  onClose: () => void;
  sessionKey: string;
  onAskAgain: (question: string) => void;
  canAsk: boolean;
}) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoad({ state: "loading" });
    setSelectedId(null);
    void (async () => {
      try {
        const response = await fetch(`/api/engineer/events?session_id=${encodeURIComponent(sessionKey)}&limit=200`, {
          signal: controller.signal,
          cache: "no-store",
        });
        const body: unknown = await response.json().catch(() => null);
        const page = response.ok ? parseEngineerEventPage(body) : null;
        if (controller.signal.aborted) return;
        if (page) setLoad({ state: "ready", events: page.events });
        else {
          const reason = (body as { reason?: unknown } | null)?.reason;
          setLoad({ state: "failed", message: humanReason(typeof reason === "string" ? reason : "engineer_activity_unavailable") });
        }
      } catch {
        if (!controller.signal.aborted) setLoad({ state: "failed", message: humanReason("local_engine_unavailable") });
      }
    })();
    return () => controller.abort();
  }, [open, sessionKey]);

  const questions = load.state === "ready" ? load.events.filter((event) => event.kind === "question").sort((a, b) => b.id - a.id) : [];
  const selected = questions.find((event) => event.id === selectedId) ?? null;
  const response =
    selected && load.state === "ready" ? load.events.find((event) => event.kind === "response" && event.relatedId === selected.id) : null;

  return (
    <Drawer open={open} onClose={onClose} title="Question history">
      {load.state === "loading" && (
        <div aria-busy="true" className={styles.historyLoading}>
          <Bone height={44} />
          <Bone height={44} />
          <Bone height={44} />
        </div>
      )}
      {load.state === "failed" && <InlineAlert tone="warning">{load.message}</InlineAlert>}
      {load.state === "ready" && questions.length === 0 && (
        <EmptyState size="card" title="No questions yet" body="Questions you ask the engineer about this session appear here." />
      )}
      {selected ? (
        <div className={styles.historyDetail}>
          <button type="button" className={styles.backButton} onClick={() => setSelectedId(null)}>
            All questions
          </button>
          <p className={styles.historyQuestion}>{selected.text}</p>
          <p className={styles.historyMeta}>
            {when(selected.createdAtUtc)}
            {selected.lap !== null ? ` · Lap ${selected.lap}` : ""}
          </p>
          <h3 className={styles.historyHeading}>Engineer</h3>
          <p className={styles.historyAnswer}>{response ? response.text : "No response was logged for this question."}</p>
          <p className={styles.historyMeta}>From the engineer log; evidence sections are not stored.</p>
          <Button variant="outline-blue" disabled={!canAsk} title={canAsk ? undefined : "Select a lap first"} onClick={() => onAskAgain(selected.text)}>
            Ask again
          </Button>
        </div>
      ) : (
        questions.length > 0 && (
          <ul className={styles.historyList}>
            {questions.map((event) => (
              <li key={event.id}>
                <button type="button" className={styles.historyRow} onClick={() => setSelectedId(event.id)}>
                  <span className={styles.historyText}>{event.text}</span>
                  <span className={styles.historyMeta}>
                    {when(event.createdAtUtc)}
                    {event.lap !== null ? ` · Lap ${event.lap}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )
      )}
    </Drawer>
  );
}
