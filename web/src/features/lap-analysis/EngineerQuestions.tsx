"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { lapAnalysisFixture } from "@/features/review-data/fixtures";
import { formatLapTime } from "@/features/review-data/format";
import {
  CheckIcon,
  EngineerAvatarIcon,
  HistoryIcon,
  PlusIcon,
  SendIcon,
  StatusGlyph,
  ThumbDownIcon,
  ThumbUpIcon,
} from "@/features/ui-foundation/icons";
import { Button, Card, Checkbox, IconButton } from "@/features/ui-foundation/primitives";
import { useToast } from "@/features/ui-foundation/Toast";
import { useAnalysis } from "./analysis-context";
import {
  COUNTER_FROM,
  FAILURE_COPY,
  V1_QUESTION_LIMIT,
  V2_QUESTION_LIMIT,
  pendingCopy,
  suggestedQuestions,
  type AnswerView,
} from "./answer";
import { BLOCKED_COPY, planAsk, runAsk, type AskContext, type AskLap } from "./ask-client";
import {
  FEEDBACK_REASONS,
  FEEDBACK_SAVED,
  FEEDBACK_UP,
  findFeedback,
  loadFeedback,
  saveFeedback,
  type FeedbackEntry,
  type FeedbackReason,
} from "./feedback";
import { QuestionHistory } from "./QuestionHistory";
import { parseThread, serializeThread, threadKey, trimThread, type StoredTurn, type TurnOutcome } from "./thread";
import styles from "./LapAnalysis.module.css";
import qStyles from "./Questions.module.css";

const ASK_BLOCKED = "Questions are not available for this lap yet. The engineer can only answer about laps imported into the local engine.";
const REFERENCE_ONLY = "Questions need a recorded lap from the local engine. This session is reference data, so no answer was generated.";

function nowLabel(date = new Date()) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function TypingIndicator() {
  return (
    <svg className={qStyles.typing} viewBox="0 0 24 8" width="24" height="8" aria-hidden="true" focusable="false">
      <circle cx="4" cy="4" r="2.4" />
      <circle cx="12" cy="4" r="2.4" />
      <circle cx="20" cy="4" r="2.4" />
    </svg>
  );
}

type Pending = { id: string; question: string; askedAt: string; targetLap: number; referenceLap: number | null; since: number };

const STATUS: Record<string, { label: string; tone: "green" | "amber" | "grey" | "red" }> = {
  answered: { label: "Answered", tone: "green" },
  partial: { label: "Partial answer", tone: "amber" },
  unsupported: { label: "Not supported", tone: "grey" },
  unavailable: { label: "Unavailable", tone: "amber" },
  busy: { label: "Busy", tone: "amber" },
  timeout: { label: "Timed out", tone: "red" },
  forbidden: { label: "Not allowed", tone: "red" },
  offline: { label: "Offline", tone: "red" },
  invalid: { label: "Not sent", tone: "red" },
  cancelled: { label: "Cancelled", tone: "grey" },
};

function statusOf(outcome: TurnOutcome): string {
  if (outcome.kind === "cancelled") return "cancelled";
  if (outcome.kind === "failure") return outcome.failure.kind;
  return outcome.view.status;
}

function fixtureView(laps: { lap: number; ms: number }[]): AnswerView {
  const fixture = lapAnalysisFixture.question;
  const time = (lap: number, fallback: number) => formatLapTime(laps.find((item) => item.lap === lap)?.ms ?? fallback);
  return {
    status: "answered",
    message: fixture.answer,
    scope: ["Turn 6 – Turn 8 (2,340 m – 2,980 m)"],
    evidence: ["Speed trace, throttle input, steering input, time delta"],
    relatedLaps: [
      { role: "Target", lap: lapAnalysisFixture.target.lap, time: time(lapAnalysisFixture.target.lap, lapAnalysisFixture.target.ms) },
      { role: "Reference", lap: lapAnalysisFixture.reference.lap, time: time(lapAnalysisFixture.reference.lap, lapAnalysisFixture.reference.ms) },
    ],
    limitations: ["Only in-game telemetry available. No tyre core temperatures or suspension data."],
    moreLimitations: [],
    model: null,
    reason: null,
    hint: null,
  };
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.answerSection}>
      <div className={styles.answerLabel}>{label}</div>
      {children}
    </div>
  );
}

function AnswerSections({ view, showModel }: { view: AnswerView; showModel: boolean }) {
  const [more, setMore] = useState(false);
  const limitations = (
    <Section label="Limitations">
      {view.limitations.map((line) => (
        <div key={line} className={styles.answerLine}>
          {view.limitations.length > 1 || view.moreLimitations.length > 0 ? `• ${line}` : line}
        </div>
      ))}
      {more &&
        view.moreLimitations.map((line) => (
          <div key={line} className={styles.answerLine}>
            {`• ${line}`}
          </div>
        ))}
      {view.moreLimitations.length > 0 && (
        <button type="button" className={qStyles.moreButton} aria-expanded={more} onClick={() => setMore((current) => !current)}>
          {more ? "Show fewer" : `+${view.moreLimitations.length} more`}
        </button>
      )}
    </Section>
  );
  const hasLimitations = view.limitations.length > 0;
  return (
    <>
      {view.status === "partial" && hasLimitations && limitations}
      {view.scope.length > 0 && (
        <Section label="Scope">
          {view.scope.map((line) => (
            <div key={line} className={styles.answerLine}>
              {line}
            </div>
          ))}
        </Section>
      )}
      {view.evidence.length > 0 && (
        <Section label="Evidence">
          {view.evidence.map((line) => (
            <div key={line} className={styles.answerLine}>
              {line}
            </div>
          ))}
        </Section>
      )}
      {view.relatedLaps.length > 0 && (
        <Section label="Related Laps">
          {view.relatedLaps.map((item) => (
            <div key={item.role} className={styles.answerLine}>
              {item.role}: Lap {item.lap} ({item.time})
            </div>
          ))}
        </Section>
      )}
      {view.status !== "partial" && hasLimitations && limitations}
      {showModel && (view.status === "answered" || view.status === "partial") && (
        <Section label="Model">
          <div className={styles.answerLine}>
            {view.model
              ? `${view.model.name}${view.model.latencyMs !== null ? ` · ${(view.model.latencyMs / 1000).toFixed(1)} s` : ""}`
              : "Measured answer (no model used)"}
          </div>
        </Section>
      )}
    </>
  );
}

export function EngineerQuestions() {
  const { model, target, reference, laps, selectedChoice, regionModel, targetTrace, referenceTrace } = useAnalysis();
  const toast = useToast();
  const isFixture = model.source === "fixture";
  const key = threadKey(model.sessionKey, target?.id ?? "none", reference?.id ?? null);

  const seed = useMemo<StoredTurn[]>(() => {
    if (!isFixture) return [];
    const fixture = lapAnalysisFixture.question;
    return [
      {
        id: "fixture",
        question: fixture.prompt,
        askedAt: fixture.askedAt,
        targetLap: lapAnalysisFixture.target.lap,
        referenceLap: lapAnalysisFixture.reference.lap,
        outcome: { kind: "answer", view: fixtureView(laps) },
      },
    ];
  }, [isFixture, laps]);

  const [thread, setThread] = useState<{ key: string; turns: StoredTurn[] }>({ key, turns: seed });
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState("");
  const [allowProcessing, setAllowProcessing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const [slow, setSlow] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  // Switching laps switches threads; stored threads come back from this tab's session storage.
  if (thread.key !== key) {
    setThread({ key, turns: isFixture ? seed : [] });
    setLoaded(false);
    setPending(null);
  }
  useEffect(() => {
    if (isFixture) {
      setLoaded(true);
      return;
    }
    let stored: StoredTurn[] = [];
    try {
      stored = parseThread(window.sessionStorage.getItem(key));
    } catch {
      stored = [];
    }
    setThread({ key, turns: stored });
    setLoaded(true);
  }, [key, isFixture]);
  useEffect(() => {
    if (isFixture || !loaded || thread.key !== key) return;
    try {
      window.sessionStorage.setItem(key, serializeThread(thread.turns));
    } catch {
      // Storage may be full or blocked; the thread then only lives in memory.
    }
  }, [thread, key, loaded, isFixture]);
  useEffect(() => {
    try {
      setFeedback(loadFeedback(window.localStorage));
    } catch {
      setFeedback([]);
    }
  }, []);
  useEffect(() => {
    if (!pending) {
      setSlow(false);
      return;
    }
    const timer = setTimeout(() => setSlow(true), 3000);
    return () => clearTimeout(timer);
  }, [pending]);
  useEffect(() => {
    const element = threadRef.current;
    if (element && atBottom.current) element.scrollTop = element.scrollHeight;
  }, [thread.turns.length, pending]);

  const turns = thread.turns;
  const canAsk = !!target && (model.source === "v2" ? !!target.revisionId : model.source === "v1" ? !!target.attemptKey : false);
  const askBlocked = model.live && !canAsk;
  const limit = model.source === "v1" ? V1_QUESTION_LIMIT : V2_QUESTION_LIMIT;
  const counterFrom = model.source === "v1" ? limit - 200 : COUNTER_FROM;
  const needsProcessing =
    model.source === "v2" &&
    [
      [target, targetTrace],
      [reference, referenceTrace],
    ].some(([lap, trace]) => {
      const item = lap as typeof target;
      const state = trace as typeof targetTrace;
      return !!item && (item.traceAvailable === false || (state?.state === "unavailable" && state.processable));
    });

  const context: AskContext = {
    source: model.source,
    sessionKey: model.sessionKey,
    live: model.live,
    runtimeReady: model.runtimeReady,
    sessionType: model.sessionType,
    region:
      model.source === "v1" && selectedChoice && !selectedChoice.complex && regionModel
        ? { modelId: regionModel.modelId, revision: regionModel.revision, regionId: selectedChoice.members[0].id }
        : null,
  };

  function append(turn: StoredTurn) {
    setThread((current) => ({ key: current.key, turns: trimThread([...current.turns, turn]) }));
  }

  async function ask(question: string) {
    const text = question.trim();
    if (!text || pending || !target) return;
    const askedAt = nowLabel();
    const id = `${Date.now()}`;
    const base = { id, question: text, askedAt, targetLap: target.lap, referenceLap: reference?.lap ?? null };
    if (isFixture) {
      append({
        ...base,
        outcome: { kind: "answer", view: { ...fixtureView(laps), status: "unavailable", message: REFERENCE_ONLY, scope: [], evidence: [], relatedLaps: [], limitations: [], reason: null } },
      });
      return;
    }
    const plan = planAsk(context, text, target, reference, allowProcessing && needsProcessing);
    if (plan.kind === "blocked") {
      append({
        ...base,
        outcome: { kind: "failure", failure: { kind: "unavailable", message: plan.reason === BLOCKED_COPY ? ASK_BLOCKED : plan.reason, retryable: false, reason: null } },
      });
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setPending({ ...base, since: Date.now() });
    setAnnouncement(pendingCopy(target.lap, reference?.lap ?? null));
    const outcome = await runAsk(plan, laps as AskLap[], { signal: controller.signal });
    abortRef.current = null;
    setPending(null);
    const stored: TurnOutcome = outcome.kind === "cancelled" ? { kind: "cancelled" } : outcome;
    append({ ...base, outcome: stored });
    setAnnouncement(outcome.kind === "answer" ? "Answer received" : outcome.kind === "cancelled" ? FAILURE_COPY.cancelled : "The question could not be answered");
  }

  function submit() {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    void ask(text);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  function retry(turn: StoredTurn) {
    setThread((current) => ({ key: current.key, turns: current.turns.filter((item) => item.id !== turn.id) }));
    void ask(turn.question);
  }

  function rate(turn: StoredTurn, rating: "up" | "down", reason: FeedbackReason | null) {
    const entry: FeedbackEntry = {
      question: turn.question,
      session: model.sessionKey,
      targetLap: turn.targetLap,
      referenceLap: turn.referenceLap,
      rating,
      reason,
      at: new Date().toISOString(),
    };
    let saved = false;
    try {
      saved = saveFeedback(window.localStorage, entry);
    } catch {
      saved = false;
    }
    setFeedback((current) => [...current.filter((item) => !(item.question === entry.question && item.session === entry.session && item.targetLap === entry.targetLap && item.referenceLap === entry.referenceLap)), entry]);
    if (saved && (rating === "up" || reason)) toast.push({ tone: "success", title: "Feedback saved on this computer" });
  }

  const showComposer = composing || (!isFixture && turns.length > 0) || (isFixture && turns.some((turn) => turn.id !== "fixture"));
  const last = turns.at(-1);
  const suggestions =
    !isFixture && canAsk && !pending
      ? suggestedQuestions({ targetLap: target?.lap ?? null, referenceLap: reference?.lap ?? null })
      : [];
  const showChips = suggestions.length > 0 && (turns.length === 0 || (last && last.outcome.kind !== "cancelled"));

  return (
    <Card
      title="Engineer Questions"
      labelledBy="engineer-questions"
      className={styles.questions}
      aside={
        <span className={qStyles.asideButtons}>
          {model.live && (
            <IconButton label="Question history" onClick={() => setHistoryOpen(true)}>
              <HistoryIcon size={15} />
            </IconButton>
          )}
          <Button
            variant="outline-blue"
            className={styles.newQuestion}
            disabled={askBlocked || !target}
            title={askBlocked ? ASK_BLOCKED : !target ? "Select a lap first" : undefined}
            onClick={() => {
              setComposing(true);
              setTimeout(() => composerRef.current?.focus(), 0);
            }}
          >
            <PlusIcon size={13} />
            New Question
          </Button>
        </span>
      }
    >
      <span className="sr-only" role="status" aria-live="polite">
        {announcement}
      </span>
      <div
        ref={threadRef}
        className={`${styles.thread} ${qStyles.thread}`}
        onScroll={(event) => {
          const element = event.currentTarget;
          atBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24;
        }}
      >
        {turns.length === 0 && !composing && !pending && (
          <p className={styles.threadEmpty}>
            {askBlocked ? (
              ASK_BLOCKED
            ) : (
              <>
                Ask about {target ? `Lap ${target.lap}` : "this lap"}
                {reference ? ` compared with Lap ${reference.lap}` : ""}.
              </>
            )}
          </p>
        )}
        {turns.map((turn, index) => {
          const status = statusOf(turn.outcome);
          const pill = STATUS[status];
          const view = turn.outcome.kind === "answer" ? turn.outcome.view : null;
          const text =
            turn.outcome.kind === "answer"
              ? turn.outcome.view.message
              : turn.outcome.kind === "failure"
                ? turn.outcome.failure.message
                : FAILURE_COPY.cancelled;
          const given = findFeedback(feedback, { question: turn.question, session: model.sessionKey, targetLap: turn.targetLap, referenceLap: turn.referenceLap });
          const canRate = view && (view.status === "answered" || view.status === "partial") && !isFixture;
          return (
            <div key={turn.id} className={styles.turn}>
              <div className={styles.userBubble}>{turn.question}</div>
              <div className={styles.askedAt}>{turn.askedAt}</div>
              <div className={styles.answer}>
                <span className={styles.avatar} aria-hidden="true">
                  <EngineerAvatarIcon size={15} />
                </span>
                <div>
                  <div className={styles.answerName}>Engineer</div>
                  <p className={styles.answerText}>{text}</p>
                  {view?.hint && <p className={qStyles.hint}>{view.hint}</p>}
                  {index === 0 && !isFixture && <p className={qStyles.hint}>Each question is answered on its own; earlier answers are not used as context.</p>}
                </div>
              </div>
              {view && (view.status === "answered" || view.status === "partial") && <AnswerSections view={view} showModel={!isFixture} />}
              <div className={styles.questionFooter}>
                <span className={styles.answered} data-status={status} data-tone={pill.tone}>
                  <StatusGlyph tone={pill.tone === "grey" ? "grey" : pill.tone} glyph={pill.tone === "green" ? "check" : "alert"} size={14} />
                  {pill.label}
                </span>
                {turn.outcome.kind === "failure" && turn.outcome.failure.retryable && (
                  <Button variant="muted" onClick={() => retry(turn)} disabled={!!pending}>
                    Try again
                  </Button>
                )}
                {(isFixture ? view?.status === "answered" && turn.id === "fixture" : canRate) && (
                  <HelpfulRow
                    given={given}
                    onRate={(rating, reason) => (isFixture ? undefined : rate(turn, rating, reason))}
                    local={isFixture}
                  />
                )}
              </div>
            </div>
          );
        })}
        {pending && (
          <div className={styles.turn}>
            <div className={styles.userBubble}>{pending.question}</div>
            <div className={styles.askedAt}>{pending.askedAt}</div>
            <div className={styles.answer} aria-busy="true">
              <span className={styles.avatar} aria-hidden="true">
                <EngineerAvatarIcon size={15} />
              </span>
              <div>
                <div className={styles.answerName}>Engineer</div>
                <p className={styles.answerText}>
                  <TypingIndicator /> {pendingCopy(pending.targetLap, pending.referenceLap)}
                </p>
                {slow && (
                  <button type="button" className={qStyles.cancelButton} onClick={() => abortRef.current?.abort()}>
                    Cancel
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
      {showChips && (
        <div className={qStyles.suggestions} role="group" aria-label="Suggested questions">
          {suggestions.map((question) => (
            <button key={question} type="button" className={qStyles.suggestion} onClick={() => void ask(question)}>
              {question}
            </button>
          ))}
        </div>
      )}
      {showComposer && (
        <form
          className={styles.composer}
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <label htmlFor="question" className="sr-only">
            Your question
          </label>
          <textarea
            id="question"
            ref={composerRef}
            value={draft}
            maxLength={limit}
            rows={3}
            placeholder={`Ask about Lap ${target?.lap ?? ""}${reference ? ` vs Lap ${reference.lap}` : ""}…`}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            disabled={askBlocked}
          />
          {draft.length >= counterFrom && (
            <div className={qStyles.counter} aria-live="polite">
              {draft.length}/{limit}
            </div>
          )}
          {selectedChoice && !isFixture && (
            <p className={qStyles.hint}>
              {context.region
                ? `Answers cover ${selectedChoice.label}.`
                : "Answers cover the full lap; the selected region is not sent."}
            </p>
          )}
          {needsProcessing && (
            <Checkbox label="Process unprocessed telemetry if needed" checked={allowProcessing} onChange={setAllowProcessing} />
          )}
          <div className={styles.composerActions}>
            {composing && (isFixture || turns.length === 0) && (
              <Button variant="muted" onClick={() => setComposing(false)}>
                Cancel
              </Button>
            )}
            <Button variant="primary" type="submit" disabled={!draft.trim() || !!pending || askBlocked}>
              <SendIcon size={13} />
              Send
            </Button>
          </div>
        </form>
      )}
      <QuestionHistory
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        sessionKey={model.sessionKey}
        onAskAgain={(question) => {
          setHistoryOpen(false);
          void ask(question);
        }}
        canAsk={canAsk && !pending}
      />
    </Card>
  );
}

function HelpfulRow({
  given,
  onRate,
  local,
}: {
  given: FeedbackEntry | null;
  onRate: (rating: "up" | "down", reason: FeedbackReason | null) => void;
  local: boolean;
}) {
  // The reference answer has no stored feedback; its buttons only toggle locally.
  const [shown, setShown] = useState<"up" | "down" | null>(null);
  const rating = local ? shown : (given?.rating ?? null);
  const choose = (next: "up" | "down") => {
    if (local) setShown(next);
    onRate(next, null);
  };
  return (
    <span className={qStyles.helpfulWrap}>
      <span className={styles.helpful}>
        Was this helpful?
        <IconButton label="Helpful" aria-pressed={rating === "up"} className={styles.thumb} onClick={() => choose("up")}>
          {rating === "up" ? <CheckIcon size={14} /> : <ThumbUpIcon size={14} />}
        </IconButton>
        <IconButton label="Not helpful" aria-pressed={rating === "down"} className={`${styles.thumb} ${styles.thumbDown}`} onClick={() => choose("down")}>
          <ThumbDownIcon size={14} />
        </IconButton>
      </span>
      {!local && rating === "up" && <span className={qStyles.thanks}>{FEEDBACK_UP}</span>}
      {!local && rating === "down" && (
        <span className={qStyles.reasons} role="group" aria-label="What was wrong?">
          {FEEDBACK_REASONS.map((reason) => (
            <button key={reason} type="button" className={qStyles.reason} aria-pressed={given?.reason === reason} onClick={() => onRate("down", reason)}>
              {reason}
            </button>
          ))}
          {given?.reason && <span className={qStyles.thanks}>{FEEDBACK_SAVED}</span>}
        </span>
      )}
    </span>
  );
}
