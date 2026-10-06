"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  EngineerAskResult,
  EngineerAskSelection,
  EngineerQueryReport,
  LapDebrief,
  TrackModelRecord,
} from "@/lib/api";
import { buildRecordedSpeechPlan } from "@/lib/recorded-speech-plan";
import {
  parseEngineerApiFailure,
  parseEngineerAskResult,
  type EngineerAskDebriefValidation,
} from "@/lib/engineer-ask";
import type { LapDebriefAttemptIdentity } from "@/lib/lap-debrief-match";
import RecordedEvidenceSpeech from "./RecordedEvidenceSpeech";

type QuestionTurn = {
  id: number;
  question: string;
  result: EngineerAskResult;
};

type Props = {
  selection: EngineerAskSelection;
  targetHref: string | null;
  referenceHref: string | null;
  validationReport: EngineerQueryReport | null;
  targetAttempt: LapDebriefAttemptIdentity | null;
  referenceAttempt: LapDebriefAttemptIdentity | null;
  selectedModel: TrackModelRecord | null;
};

const MAX_TURNS = 20;
const MAX_QUESTION_BYTES = 1_024;
const REQUEST_TIMEOUT_MS = 60_000;

export default function EngineerQuestionComposer({
  selection,
  targetHref,
  referenceHref,
  validationReport,
  targetAttempt,
  referenceAttempt,
  selectedModel,
}: Props) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<QuestionTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const requestGeneration = useRef(0);
  const nextId = useRef(0);
  const questionBytes = useMemo(
    () => new TextEncoder().encode(question).byteLength,
    [question],
  );
  const selectionKey = JSON.stringify(selection);

  useEffect(
    () => () => {
      requestGeneration.current += 1;
      activeRequest.current?.abort();
      activeRequest.current = null;
    },
    [selectionKey],
  );

  const cancel = () => {
    requestGeneration.current += 1;
    const controller = activeRequest.current;
    activeRequest.current = null;
    controller?.abort();
    setBusy(false);
  };

  const clear = () => {
    cancel();
    setTurns([]);
    setQuestion("");
    setError(null);
  };

  const ask = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy || turns.length >= MAX_TURNS) return;
    const trimmed = question.trim();
    if (!trimmed) {
      setError("Enter a question about the selected recorded evidence.");
      return;
    }
    if (new TextEncoder().encode(trimmed).byteLength > MAX_QUESTION_BYTES) {
      setError("Keep the question under 1 KiB of text.");
      return;
    }

    const controller = new AbortController();
    activeRequest.current?.abort();
    activeRequest.current = controller;
    const generation = ++requestGeneration.current;
    const isCurrent = () =>
      requestGeneration.current === generation &&
      activeRequest.current === controller;
    const deadline = window.setTimeout(
      () => controller.abort(),
      REQUEST_TIMEOUT_MS,
    );
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/engineer/ask", {
        method: "POST",
        cache: "no-store",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: trimmed, selection }),
        signal: controller.signal,
      });
      const envelope: unknown = await response.json();
      if (!isCurrent()) return;
      if (!response.ok) {
        setError(
          parseEngineerApiFailure(envelope) ??
            "The local question could not be completed. Check the runtime and retry.",
        );
        return;
      }
      if (
        !isApiEnvelope(envelope) ||
        envelope.status !== "ok" ||
        !envelope.data
      ) {
        setError("GP...T returned a response that could not be verified.");
        return;
      }
      const validation: EngineerAskDebriefValidation = {
        report: validationReport,
        target: targetAttempt,
        reference: referenceAttempt,
        model: selectedModel,
      };
      const result = parseEngineerAskResult(
        envelope.data,
        selection,
        validation,
      );
      if (!isCurrent()) return;
      if (!result) {
        setError(
          "The answer did not match the exact selected attempt and its evidence. No answer was shown.",
        );
        return;
      }
      setTurns((current) => {
        if (
          requestGeneration.current !== generation ||
          current.length >= MAX_TURNS
        ) {
          return current;
        }
        return [
          ...current,
          { id: ++nextId.current, question: trimmed, result },
        ];
      });
      if (isCurrent()) setQuestion("");
    } catch {
      if (isCurrent()) {
        setError(
          controller.signal.aborted
            ? "The question timed out or was cancelled. The selected evidence is unchanged."
            : "The local question service could not be reached. Check Settings, then retry.",
        );
      }
    } finally {
      window.clearTimeout(deadline);
      if (isCurrent()) {
        activeRequest.current = null;
        setBusy(false);
      }
    }
  };

  return (
    <section
      className="engineer-ask-panel panel"
      aria-labelledby="engineer-ask-title"
    >
      <header className="engineer-ask-heading">
        <div>
          <span className="eyebrow">GP...T · SELECTED EVIDENCE</span>
          <h2 id="engineer-ask-title">Ask GP...T</h2>
          <p>
            Ask about this exact recorded selection. A local model routes the
            question; the measurements, source links, and limitations come from
            the stored reports.
          </p>
        </div>
        <span className="diagnostic-tag">NO COACHING</span>
      </header>

      <p className="engineer-ask-privacy">
        Questions stay in this tab. Only the current question and the available
        report type go to the local model; telemetry facts and earlier turns do
        not.
      </p>

      {error ? (
        <p className="engineer-ask-error" role="status">
          {error}
        </p>
      ) : null}

      <form className="engineer-ask-form" onSubmit={ask}>
        <label htmlFor="engineer-ask-question">Your question</label>
        <textarea
          id="engineer-ask-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          maxLength={1_024}
          rows={3}
          placeholder="What does the selected lap show?"
          disabled={busy || turns.length >= MAX_TURNS}
        />
        <div className="engineer-ask-form-footer">
          <span aria-live="polite">
            {questionBytes.toLocaleString()} /{" "}
            {MAX_QUESTION_BYTES.toLocaleString()} UTF-8 bytes
          </span>
          <div>
            {busy ? (
              <button
                className="button-tertiary"
                type="button"
                onClick={cancel}
              >
                Cancel
              </button>
            ) : null}
            <button
              className="button-secondary"
              type="submit"
              disabled={
                busy ||
                questionBytes > MAX_QUESTION_BYTES ||
                turns.length >= MAX_TURNS
              }
            >
              {busy ? "Checking selected evidence…" : "Ask"}
            </button>
          </div>
        </div>
      </form>

      {turns.length > 0 ? (
        <div className="engineer-ask-history">
          <div className="engineer-ask-history-heading">
            <span>
              {turns.length} / {MAX_TURNS} in this tab
            </span>
            <button className="button-tertiary" type="button" onClick={clear}>
              Clear conversation
            </button>
          </div>
          <ol
            className="engineer-ask-turns"
            aria-label="Questions about selected evidence"
          >
            {turns.map((turn) => (
              <li key={turn.id}>
                <div className="engineer-ask-question">
                  <span>YOU</span>
                  <p>{turn.question}</p>
                </div>
                <EngineerAskAnswer
                  result={turn.result}
                  targetHref={targetHref}
                  referenceHref={referenceHref}
                  selection={selection}
                />
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <p className="engineer-ask-empty">
          No conversation is stored. Clear this tab or leave the page to remove
          these turns.
        </p>
      )}
    </section>
  );
}

function EngineerAskAnswer({
  result,
  targetHref,
  referenceHref,
  selection,
}: {
  result: EngineerAskResult;
  targetHref: string | null;
  referenceHref: string | null;
  selection: EngineerAskSelection;
}) {
  const speechPlan = result.report
    ? buildRecordedSpeechPlan({ kind: "engineer_query", report: result.report })
    : result.debrief && selection.intent === "region_comparison"
      ? buildRecordedSpeechPlan({
          kind: "lap_debrief",
          report: result.debrief,
          targetAttemptKey: selection.target_attempt_key,
          referenceAttemptKey: selection.reference_attempt_key,
        })
      : null;

  return (
    <article className="engineer-ask-answer" aria-label="GP...T answer">
      <div className="engineer-ask-answer-title">
        <span>GP...T</span>
        <strong>
          {result.status === "unavailable"
            ? "EVIDENCE UNAVAILABLE"
            : result.status === "unsupported"
              ? "OUTSIDE SELECTED EVIDENCE"
              : result.status === "partial"
                ? "PARTIAL EVIDENCE"
                : "RECORDED EVIDENCE"}
        </strong>
      </div>
      <p className="engineer-ask-answer-message">{result.message}</p>
      <div className="engineer-ask-sources">
        {targetHref ? <a href={targetHref}>Open target attempt ↗</a> : null}
        {referenceHref ? (
          <a href={referenceHref}>Open reference attempt ↗</a>
        ) : null}
      </div>
      {result.report ? <EngineerAskReport report={result.report} /> : null}
      {result.debrief ? <EngineerAskDebrief debrief={result.debrief} /> : null}
      {result.model ? (
        <p className="engineer-ask-model">
          Local {result.model.model_name} ·{" "}
          {result.model.model_digest.slice(0, 19)}… ·{" "}
          {result.model.inference_placement} ·{" "}
          {(result.model.latency_ms / 1_000).toFixed(1)} s
        </p>
      ) : null}
      {speechPlan ? <RecordedEvidenceSpeech planResult={speechPlan} /> : null}
    </article>
  );
}

function EngineerAskReport({ report }: { report: EngineerQueryReport }) {
  return (
    <div className="engineer-ask-evidence">
      {report.facts.length > 0 ? (
        <ul className="engineer-query-facts">
          {report.facts.map((fact, index) => (
            <li key={`${fact.kind}-${index}`}>
              <span>{fact.kind.replaceAll("_", " ")}</span>
              <p>{fact.text}</p>
              <details>
                <summary>Evidence fields</summary>
                <code>{fact.source_fields.join(" · ")}</code>
              </details>
            </li>
          ))}
        </ul>
      ) : null}
      {report.warnings.length > 0 ? (
        <ul
          className="engineer-query-warnings"
          aria-label="Evidence qualifications"
        >
          {report.warnings.map((warning, index) => (
            <li key={`${warning.code}-${index}`}>
              {warning.sides?.length ? (
                <strong>{warning.sides.join(" / ")} · </strong>
              ) : null}
              {warning.text}
              <details>
                <summary>Qualification source</summary>
                <code>{warning.source_fields.join(" · ")}</code>
              </details>
            </li>
          ))}
        </ul>
      ) : null}
      {report.omitted_fact_count > 0 || report.omitted_warning_count > 0 ? (
        <p className="engineer-query-omissions">
          Other facts omitted for this focus: {report.omitted_fact_count};
          qualifications omitted by the report bound:{" "}
          {report.omitted_warning_count}.
        </p>
      ) : null}
      <p className="engineer-query-footnote">
        Diagnostic evidence only. It does not rank laps or recommend driving
        changes.
      </p>
    </div>
  );
}

function EngineerAskDebrief({ debrief }: { debrief: LapDebrief }) {
  return (
    <div className="engineer-ask-evidence engineer-ask-debrief">
      {debrief.official_lap_time ? (
        <section>
          <span>OFFICIAL LAP TIME</span>
          <p>{debrief.official_lap_time.text}</p>
          <details>
            <summary>Evidence source</summary>
            <code>
              {Object.values(debrief.official_lap_time.source_fields).join(
                " · ",
              )}
            </code>
          </details>
        </section>
      ) : null}
      {debrief.ranked_regions.map((region) => (
        <section key={`${region.rank}-${region.region_id}`}>
          <span>
            QUALIFIED REGION {region.rank} · {region.region_id}
          </span>
          <p>{region.text}</p>
          <details>
            <summary>Evidence source</summary>
            <code>{Object.values(region.source_fields).join(" · ")}</code>
          </details>
        </section>
      ))}
      {debrief.limitations.length > 0 ? (
        <ul
          className="engineer-query-warnings"
          aria-label="Debrief limitations"
        >
          {debrief.limitations.map((item) => (
            <li key={item.code}>{item.text}</li>
          ))}
        </ul>
      ) : null}
      <p className="engineer-query-footnote">{debrief.text}</p>
    </div>
  );
}

function isApiEnvelope(value: unknown): value is {
  api_version: "v1";
  status: "ok" | "unavailable";
  data: unknown;
} {
  return Boolean(
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).api_version === "v1" &&
    (value as Record<string, unknown>).status === "ok" &&
    "data" in value,
  );
}
