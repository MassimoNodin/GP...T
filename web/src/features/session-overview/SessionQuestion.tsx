"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { askFailure, type AnswerView, type AskFailure } from "@/features/lap-analysis/answer";
import { planAsk, runAsk, type AskLap } from "@/features/lap-analysis/ask-client";
import { useRuntimeFeed } from "@/features/live-status/hooks";
import { formatLapTime } from "@/features/review-data/format";
import { EngineerAvatarIcon, StatusGlyph } from "@/features/ui-foundation/icons";
import { Button, Card } from "@/features/ui-foundation/primitives";
import { analysisHref, isCleanLap } from "./overview-data";
import type { OverviewLap } from "./types";
import type { ViewData } from "./view-types";
import viewStyles from "./OverviewViews.module.css";

const LIMIT = 1000;

/** One-click session questions; the engineer answers them from the selected lap against the session best. */
export const SESSION_SUGGESTIONS = [
  "Summarise this session and where I can improve",
  "How consistent was my pace?",
  "Where am I losing time to my best lap?",
];

function askLap(lap: OverviewLap, source: "fixture" | "v1" | "v2"): AskLap {
  return {
    id: lap.id,
    lap: lap.lap,
    ms: lap.ms,
    attemptKey: source === "v1" ? lap.id : null,
    revisionId: lap.revisionId ?? null,
  };
}

type Turn =
  | { state: "pending"; question: string }
  | { state: "answered"; question: string; answer: AnswerView }
  | { state: "failed"; question: string; failure: AskFailure };

/**
 * "Ask the engineer" on the Overview: a question about the session, answered
 * from the selected lap measured against the session best (the engineer
 * reads one lap, or a pair, at a time).
 */
export function SessionQuestion({ data }: { data: ViewData }) {
  const { model, laps, selectedId, best } = data;
  const runtime = useRuntimeFeed(undefined);
  const [draft, setDraft] = useState("");
  const [turn, setTurn] = useState<Turn | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  // A pit or invalid lap cannot speak for the session; fall back to the best lap.
  const chosen = laps.find((lap) => lap.id === selectedId) ?? null;
  const selected = chosen && isCleanLap(chosen) ? chosen : (best ?? chosen);
  const reference = best && selected && best.id !== selected.id ? best : null;
  const blocked =
    model.source === "fixture"
      ? "Questions work on your own recorded sessions."
      : !selected
        ? "This session has no timed laps to ask about."
        : null;

  async function ask(question: string) {
    const text = question.trim();
    if (!text || !selected || blocked) return;
    const plan = planAsk(
      {
        source: model.source,
        sessionKey: model.sessionKey,
        live: true,
        runtimeReady: runtime?.status === "ready",
        sessionType: null,
        region: null,
      },
      text,
      askLap(selected, model.source),
      reference ? askLap(reference, model.source) : null,
      false,
    );
    if (plan.kind === "blocked") {
      setTurn({ state: "failed", question: text, failure: askFailure(422, null) });
      return;
    }
    controller.current?.abort();
    controller.current = new AbortController();
    setTurn({ state: "pending", question: text });
    setDraft("");
    const lapsForAnswer = [selected, ...(reference ? [reference] : [])].map((lap) => askLap(lap, model.source));
    const outcome = await runAsk(plan, lapsForAnswer, { signal: controller.current.signal });
    if (outcome.kind === "cancelled") return;
    setTurn(
      outcome.kind === "answer"
        ? { state: "answered", question: text, answer: outcome.view }
        : { state: "failed", question: text, failure: outcome.failure },
    );
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(draft);
  }

  const scope = selected
    ? reference
      ? `Answers use Lap ${selected.lap} (${formatLapTime(selected.ms)}) against your best, Lap ${reference.lap}.`
      : `Answers use Lap ${selected.lap} (${formatLapTime(selected.ms)}), your best lap.`
    : null;

  return (
    <Card title="Ask the Engineer" labelledBy="session-question" className={viewStyles.askCard}>
      {blocked ? (
        <p className={viewStyles.note}>{blocked}</p>
      ) : (
        <>
          <div className={viewStyles.askChips} role="group" aria-label="Suggested questions">
            {SESSION_SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className={viewStyles.askChip}
                disabled={turn?.state === "pending"}
                onClick={() => void ask(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
          <form className={viewStyles.askForm} onSubmit={submit}>
            <label htmlFor="session-question-input" className="sr-only">
              Your question about this session
            </label>
            <input
              id="session-question-input"
              type="text"
              value={draft}
              maxLength={LIMIT}
              placeholder="Ask about this session…"
              onChange={(event) => setDraft(event.target.value)}
              disabled={turn?.state === "pending"}
            />
            <Button variant="primary" type="submit" disabled={!draft.trim() || turn?.state === "pending"}>
              Ask
            </Button>
          </form>
          {scope && <p className={viewStyles.askScope}>{scope}</p>}
        </>
      )}
      {turn && (
        <div className={viewStyles.askTurn} aria-live="polite">
          <div className={viewStyles.askQuestion}>{turn.question}</div>
          <div className={viewStyles.askAnswer}>
            <span className={viewStyles.askAvatar} aria-hidden="true">
              <EngineerAvatarIcon size={14} />
            </span>
            <div>
              {turn.state === "pending" && <p>Reading the session…</p>}
              {turn.state === "failed" && (
                <p className={viewStyles.askFailed}>
                  <StatusGlyph tone="amber" glyph="alert" size={13} /> {turn.failure.message}
                </p>
              )}
              {turn.state === "answered" && (
                <>
                  <p>{turn.answer.message}</p>
                  {turn.answer.limitations.length > 0 && (
                    <ul className={viewStyles.askLimits} aria-label="Limitations">
                      {turn.answer.limitations.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
          </div>
          {turn.state !== "pending" && selected && (
            <Link href={analysisHref(model.sessionKey, selected.id, reference?.id ?? null)} className={viewStyles.askMore}>
              Continue in Lap Analysis
            </Link>
          )}
        </div>
      )}
    </Card>
  );
}
