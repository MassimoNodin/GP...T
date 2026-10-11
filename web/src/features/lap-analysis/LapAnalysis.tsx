"use client";

import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState, type ComponentProps } from "react";
import { lapAnalysisFixture } from "@/features/review-data/fixtures";
import { formatDelta, formatLapTime, formatSectorTime } from "@/features/review-data/format";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  CheckIcon,
  EngineerAvatarIcon,
  PlusIcon,
  StatusGlyph,
  ThumbDownIcon,
  ThumbUpIcon,
  ZoomIcon,
} from "@/features/ui-foundation/icons";
import {
  Button,
  Card,
  DeltaPill,
  IconButton,
  PageHeader,
  Select,
  Tabs,
} from "@/features/ui-foundation/primitives";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import type { AnalysisLap, LapAnalysisModel } from "./load";
import { axisFor, lapDifferences, lapQuality, type LapTrace } from "./lap-telemetry";
import { REFERENCE_COLOR, TARGET_COLOR, TelemetryCharts } from "./TelemetryCharts";
import styles from "./LapAnalysis.module.css";

type View = "overview" | "telemetry" | "sectors" | "map" | "inputs" | "quality";
type CompareMode = "delta" | "distance";

const VIEWS: Array<{ key: View; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "telemetry", label: "Telemetry" },
  { key: "sectors", label: "Sectors & Corners" },
  { key: "map", label: "Track Map" },
  { key: "inputs", label: "Inputs" },
  { key: "quality", label: "Data Quality" },
];

const REGIONS: Record<string, { from: number; to: number; band: { from: number; to: number; inner?: number } }> = {
  "Turn 1 – Turn 5": { from: 0, to: 1180, band: { from: 2300, to: 2300 } },
  "Turn 6 – Turn 8": {
    from: lapAnalysisFixture.region.from,
    to: lapAnalysisFixture.region.to,
    band: lapAnalysisFixture.chart.band,
  },
  "Turn 9 – Turn 10": { from: 2980, to: 3060, band: { from: 2980, to: 3060 } },
  "Turn 11 – Turn 19": { from: 3060, to: 3337, band: { from: 3060, to: 3060 } },
};

const QUICK_JUMPS: Record<string, string> = {
  S1: "Turn 1 – Turn 5",
  S2: "Turn 6 – Turn 8",
  S3: "Turn 11 – Turn 19",
  "T1-5": "Turn 1 – Turn 5",
  "T6-10": "Turn 6 – Turn 8",
};

type Turn = { id: number; question: string; askedAt: string } & (
  | { status: "answered"; answer: string; sections: Array<[string, string[]]> }
  | { status: "pending" }
  | { status: "unavailable"; answer: string }
);

function nowLabel() {
  const date = new Date();
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export function LapAnalysis({ model }: { model: LapAnalysisModel }) {
  const [view, setView] = useState<View>("overview");
  const [compare, setCompare] = useState<CompareMode>("delta");
  const router = useRouter();
  const pathname = usePathname();
  const [targetId, setTargetIdState] = useState(model.targetId);
  const [referenceId, setReferenceIdState] = useState(model.referenceId);

  // Keep the URL (and the server-rendered header context) in sync with the pickers.
  function syncUrl(nextTarget: string, nextReference: string | null) {
    const query = new URLSearchParams({ target: nextTarget });
    if (nextReference) query.set("reference", nextReference);
    router.replace(`${pathname}?${query}`, { scroll: false });
  }
  const setTargetId = (id: string) => {
    setTargetIdState(id);
    syncUrl(id, referenceId);
  };
  const setReferenceId = (id: string) => {
    setReferenceIdState(id);
    syncUrl(targetId, id);
  };
  // Real sessions carry no corner regions: the backend lists track models but not their regions.
  const measuredTelemetry = model.telemetry.kind === "real" ? model.telemetry : null;
  const [region, setRegion] = useState<string | null>(measuredTelemetry ? null : lapAnalysisFixture.region.label);

  const targetIndex = Math.max(0, model.laps.findIndex((lap) => lap.id === targetId));
  const target = model.laps[targetIndex];
  const referenceIndex = model.laps.findIndex((lap) => lap.id === referenceId);
  const reference = referenceIndex >= 0 ? model.laps[referenceIndex] : null;

  const regionInfo = region && !measuredTelemetry ? REGIONS[region] : null;
  const chart = lapAnalysisFixture.chart;
  const ticks = compare === "distance" ? [2400, 2500, 2600, 2700, 2800, 2900, 3000] : chart.ticks;
  const band = regionInfo && regionInfo.band.to > regionInfo.band.from ? regionInfo.band : null;

  // Measured traces only count once they belong to the laps shown in the pickers.
  const targetTrace: LapTrace | undefined = measuredTelemetry && target ? measuredTelemetry.traces[target.id] : undefined;
  const referenceTrace: LapTrace | undefined = measuredTelemetry && reference ? measuredTelemetry.traces[reference.id] : undefined;
  const axis = axisFor([targetTrace, referenceTrace]);
  const differences = useMemo(
    () => (measuredTelemetry ? lapDifferences(target, reference, targetTrace, referenceTrace) : []),
    [measuredTelemetry, reference, referenceTrace, target, targetTrace],
  );
  const quality = useMemo(
    () =>
      measuredTelemetry
        ? lapQuality({ target, reference, targetTrace, referenceTrace, lapDataNote: model.lapDataNote, trackModel: model.trackModel })
        : [],
    [measuredTelemetry, model.lapDataNote, model.trackModel, reference, referenceTrace, target, targetTrace],
  );

  const sectors = useMemo(() => {
    if (!target) return [];
    return [0, 1, 2].map((i) => {
      const value = target.sectors?.[i] ?? null;
      const ref = reference?.sectors?.[i] ?? null;
      return {
        label: `S${i + 1}`,
        ms: value,
        deltaMs: value !== null && ref !== null ? value - ref : null,
      };
    });
  }, [reference, target]);

  const lapDelta = target && reference ? target.ms - reference.ms : null;

  return (
    <div className={styles.layout}>
      <div className={styles.main}>
        <div className={styles.headerRow}>
          <PageHeader
            title="Lap Analysis"
            subtitle="Analyse a single lap or compare two laps with telemetry, track map and AI insights."
          />
          <div className={styles.pickers}>
            <LapPicker
              label="Target Lap"
              dot={TARGET_COLOR}
              laps={model.laps}
              index={targetIndex}
              onChange={setTargetId}
              trailing={lapDelta !== null ? <span className={styles.pickerDelta}>{formatDelta(lapDelta)}</span> : null}
            />
            <LapPicker
              label="Reference Lap"
              labelDot="var(--green)"
              dot={REFERENCE_COLOR}
              laps={model.laps}
              index={referenceIndex}
              onChange={setReferenceId}
              trailing={
                reference && reference.id === model.bestId ? (
                  <span className={styles.pickerNote}>(Session Best)</span>
                ) : null
              }
            />
          </div>
        </div>

        <div className={styles.tabRow}>
          <Tabs items={VIEWS} value={view} onChange={setView} label="Analysis views" size="sm" />
          <div className={styles.compare}>
            <span>Compare</span>
            <Tabs
              items={[
                { key: "delta", label: "Time Delta" },
                { key: "distance", label: "Distance" },
              ]}
              value={compare}
              onChange={setCompare}
              label="Comparison axis"
              size="sm"
              className={styles.compareTabs}
            />
          </div>
        </div>

        <div className={styles.content} data-view={view}>
          {(view === "overview" || view === "map") && (
            <Card className={styles.trackCard}>
              <div className={styles.trackHead}>
                <div>
                  <h2 className={styles.trackTitle}>{model.track}</h2>
                  <div className={styles.trackSub}>
                    {target && reference ? `Lap ${target.lap} vs ${reference.lap}` : target ? `Lap ${target.lap}` : ""}
                  </div>
                </div>
                <IconButton label="Enlarge track map" className={styles.zoom} onClick={() => setView(view === "map" ? "overview" : "map")}>
                  <ZoomIcon size={15} />
                </IconButton>
              </div>
              <TrackMap track={model.trackKey} className={styles.trackMap} strokeWidth={2} title={`${model.track} circuit map`} />
              {measuredTelemetry ? (
                <div className={styles.trackSection}>
                  <div className={styles.fieldLabel}>Distance</div>
                  <div className={styles.fieldValue}>
                    {axis ? `Full lap · ${axis.from.toLocaleString("en-US")} m – ${axis.to.toLocaleString("en-US")} m` : "Full lap"}
                  </div>
                  <p className={styles.unavailable}>Corner regions are not available for this session.</p>
                </div>
              ) : (
              <div className={styles.trackSection}>
                <label className={styles.fieldLabel} htmlFor="region">
                  Selected Region
                </label>
                <Select id="region" value={region ?? ""} onChange={(event) => setRegion(event.target.value || null)} className={styles.fullSelect}>
                  <option value="">Full lap</option>
                  {Object.keys(REGIONS).map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </Select>
                <div className={styles.fieldLabel}>Distance</div>
                <div className={styles.fieldValue}>
                  {regionInfo
                    ? `${regionInfo.from.toLocaleString("en-US")} m – ${regionInfo.to.toLocaleString("en-US")} m`
                    : "Full lap"}
                </div>
                <div className={styles.regionButtons}>
                  <Button onClick={() => setRegion(null)}>Full Lap</Button>
                  <Button variant="muted" onClick={() => setRegion(null)}>
                    Clear
                  </Button>
                </div>
                <div className={styles.fieldLabel}>Quick Jumps</div>
                <div className={styles.jumps}>
                  {Object.entries(QUICK_JUMPS).map(([label, target]) => (
                    <button key={label} type="button" className={styles.jump} onClick={() => setRegion(target)}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              )}
            </Card>
          )}

          {(view === "overview" || view === "telemetry" || view === "inputs") && (
            <Card className={styles.chartCard}>
              {measuredTelemetry ? (
                <MeasuredCharts
                  target={target}
                  reference={reference}
                  targetTrace={targetTrace}
                  referenceTrace={referenceTrace}
                  channels={view === "inputs" ? ["throttle", "brake", "steering"] : ["speed", "throttle", "brake", "steering"]}
                />
              ) : (
                <TelemetryCharts
                  from={chart.from}
                  to={chart.to}
                  ticks={ticks}
                  band={band}
                  channels={view === "inputs" ? ["throttle", "brake", "steering"] : ["speed", "throttle", "brake", "steering"]}
                />
              )}
            </Card>
          )}

          {(view === "overview" || view === "sectors" || view === "quality") && (
            <div className={styles.sideCol}>
              {(view === "overview" || view === "sectors") && (
                <Card title="Sector Comparison" labelledBy="sector-comparison" className={styles.sectorCard}>
                  <table className={styles.sectorTable}>
                    <tbody>
                      {sectors.map((sector) => (
                        <tr key={sector.label}>
                          <th scope="row">{sector.label}</th>
                          <td className="tabular">{formatSectorTime(sector.ms)}</td>
                          <td>
                            {sector.deltaMs !== null ? (
                              <DeltaPill ms={sector.deltaMs}>{formatDelta(sector.deltaMs)}</DeltaPill>
                            ) : (
                              <span className={styles.na}>—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                      <tr className={styles.lapRow}>
                        <th scope="row">Lap</th>
                        <td className="tabular">{formatLapTime(target?.ms)}</td>
                        <td>
                          {lapDelta !== null ? <DeltaPill ms={lapDelta}>{formatDelta(lapDelta)}</DeltaPill> : <span className={styles.na}>—</span>}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  {measuredTelemetry && target && !target.sectors && (
                    <p className={styles.unavailable}>Sector times were not recorded for this lap.</p>
                  )}
                </Card>
              )}
              {(view === "overview" || view === "sectors") && (
                <Card
                  title={measuredTelemetry ? "Key Differences (Full Lap)" : "Key Differences (Selected Region)"}
                  labelledBy="key-differences"
                  className={styles.diffCard}
                >
                  {measuredTelemetry && differences.length === 0 ? (
                    <p className={styles.unavailable}>
                      {reference ? "Differences are not available for these laps." : "Select a reference lap to see differences."}
                    </p>
                  ) : (
                    <ul className={styles.diffList}>
                      {(measuredTelemetry ? differences : lapAnalysisFixture.differences).map((item) => (
                        <li key={item.text} data-emphasis={item.emphasis ? "true" : undefined}>
                          <StatusGlyph tone="red" glyph="alert" size={13} />
                          {item.text}
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              )}
              {(view === "overview" || view === "quality") && (
                <Card title="Data Quality" labelledBy="data-quality" className={styles.qualityCard}>
                  <ul className={styles.qualityList}>
                    {(measuredTelemetry ? quality : lapAnalysisFixture.quality).map((item) => (
                      <li key={item.text}>
                        <StatusGlyph tone={item.tone} glyph="ring" size={12} />
                        {item.text}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </div>
          )}
        </div>
      </div>

      <EngineerQuestions model={model} target={target} reference={reference} />
    </div>
  );
}

/** Chart card body for real sessions: measured traces, or why there are none. */
function MeasuredCharts({
  target,
  reference,
  targetTrace,
  referenceTrace,
  channels,
}: {
  target: AnalysisLap | undefined;
  reference: AnalysisLap | null;
  targetTrace: LapTrace | undefined;
  referenceTrace: LapTrace | undefined;
  channels: ComponentProps<typeof TelemetryCharts>["channels"];
}) {
  if (!target) return <p className={styles.chartEmpty}>No lap is selected.</p>;
  // The server sends traces for the laps in the URL; the pickers can move ahead of it.
  if (!targetTrace || (reference && !referenceTrace)) {
    return <p className={styles.chartEmpty}>Loading telemetry for the selected laps…</p>;
  }
  const missing = [
    { lap: target, trace: targetTrace },
    ...(reference && referenceTrace ? [{ lap: reference, trace: referenceTrace }] : []),
  ].filter((entry) => entry.trace.state === "unavailable");
  const axis = axisFor([targetTrace, referenceTrace]);
  if (missing.length > 0 || !axis || targetTrace.state !== "available") {
    return (
      <div className={styles.chartEmpty}>
        <p>Telemetry traces are not available for this comparison.</p>
        {missing.map(({ lap, trace }) => (
          <p key={lap.id}>
            Lap {lap.lap}: {trace.state === "unavailable" ? trace.reason : ""}
          </p>
        ))}
      </div>
    );
  }
  return (
    <>
      <TelemetryCharts
        from={axis.from}
        to={axis.to}
        ticks={axis.ticks}
        band={null}
        channels={channels}
        measured={{
          target: targetTrace.channels,
          reference: referenceTrace?.state === "available" ? referenceTrace.channels : null,
        }}
      />
      {!reference && <p className={styles.chartNote}>No reference lap is selected, so only the target lap is drawn.</p>}
    </>
  );
}

function LapPicker({
  label,
  labelDot,
  dot,
  laps,
  index,
  onChange,
  trailing,
}: {
  label: string;
  labelDot?: string;
  dot: string;
  laps: AnalysisLap[];
  index: number;
  onChange: (id: string) => void;
  trailing: React.ReactNode;
}) {
  const lap = index >= 0 ? laps[index] : null;
  return (
    <div className={styles.picker} role="group" aria-label={label}>
      <span className={styles.pickerLabel}>
        {labelDot && <i style={{ background: labelDot }} className={styles.labelDot} />}
        {label}
        <ChevronRightIcon size={11} />
      </span>
      <span className={styles.pickerValue} aria-live="polite">
        <i style={{ background: dot }} className={styles.lapDot} />
        <strong>{lap ? `Lap ${lap.lap}` : "None"}</strong>
        <span className={styles.pickerTime}>{lap ? formatLapTime(lap.ms) : "—"}</span>
        {trailing}
      </span>
      <span className={styles.pickerNav}>
        <IconButton
          label={`Previous ${label.toLowerCase()}`}
          className={styles.pickerButton}
          disabled={index <= 0}
          onClick={() => onChange(laps[index - 1].id)}
        >
          <ChevronLeftIcon size={13} />
        </IconButton>
        <IconButton
          label={`Next ${label.toLowerCase()}`}
          className={styles.pickerButton}
          disabled={index < 0 || index >= laps.length - 1}
          onClick={() => onChange(laps[index + 1].id)}
        >
          <ChevronRightIcon size={13} />
        </IconButton>
      </span>
    </div>
  );
}

const ASK_BLOCKED = "Questions are not available for this lap yet. The engineer can only answer about laps imported into the local engine.";

function EngineerQuestions({
  model,
  target,
  reference,
}: {
  model: LapAnalysisModel;
  target: AnalysisLap | undefined;
  reference: AnalysisLap | null;
}) {
  const fixture = lapAnalysisFixture.question;
  const [turns, setTurns] = useState<Turn[]>(() =>
    model.live
      ? []
      : [{ id: 0, question: fixture.prompt, askedAt: fixture.askedAt, status: "answered", answer: fixture.answer, sections: fixture.sections }],
  );
  const [composing, setComposing] = useState(false);
  const [draft, setDraft] = useState("");
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  async function ask(question: string) {
    const id = Date.now();
    const askedAt = nowLabel();
    if (!target?.attemptKey) {
      setTurns((current) => [
        ...current,
        {
          id,
          question,
          askedAt,
          status: "unavailable",
          answer: "Questions need a recorded lap from the local engine. This session is reference data, so no answer was generated.",
        },
      ]);
      return;
    }
    setTurns((current) => [...current, { id, question, askedAt, status: "pending" }]);
    try {
      const response = await fetch("/api/engineer/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          question,
          selection: { intent: "attempt_summary", target_attempt_key: target.attemptKey },
        }),
      });
      const body = (await response.json().catch(() => null)) as {
        status?: string;
        data?: { status?: string; message?: string } | null;
        reason?: string | null;
      } | null;
      const message = body?.data?.message;
      const answered = body?.data?.status === "answered" || body?.data?.status === "partial";
      setTurns((current) =>
        current.map((turn) =>
          turn.id !== id
            ? turn
            : answered && message
              ? {
                  ...turn,
                  status: "answered",
                  answer: message,
                  sections: [
                    ["Scope", [`Lap ${target.lap} (${formatLapTime(target.ms)})`]],
                    ["Limitations", ["Only in-game telemetry available."]],
                  ],
                }
              : {
                  ...turn,
                  status: "unavailable",
                  answer: message ?? `The engineer could not answer${body?.reason ? ` (${body.reason.replaceAll("_", " ")})` : ""}.`,
                },
        ),
      );
    } catch {
      setTurns((current) =>
        current.map((turn) =>
          turn.id === id ? { ...turn, status: "unavailable", answer: "The local engine is unavailable." } : turn,
        ),
      );
    }
  }

  const last = turns.at(-1);
  // Real laps can only be asked about through a v1 attempt key.
  const askBlocked = model.live && !target?.attemptKey;

  return (
    <Card
      title="Engineer Questions"
      labelledBy="engineer-questions"
      className={styles.questions}
      aside={
        <Button
          variant="outline-blue"
          className={styles.newQuestion}
          disabled={askBlocked}
          title={askBlocked ? ASK_BLOCKED : undefined}
          onClick={() => setComposing(true)}
        >
          <PlusIcon size={13} />
          New Question
        </Button>
      }
    >
      <div className={styles.thread}>
        {turns.length === 0 && !composing && (
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
        {turns.map((turn) => (
          <div key={turn.id} className={styles.turn}>
            <div className={styles.userBubble}>{turn.question}</div>
            <div className={styles.askedAt}>{turn.askedAt}</div>
            <div className={styles.answer}>
              <span className={styles.avatar} aria-hidden="true">
                <EngineerAvatarIcon size={15} />
              </span>
              <div>
                <div className={styles.answerName}>Engineer</div>
                <p className={styles.answerText}>
                  {turn.status === "pending" ? "Analysing the selected lap…" : turn.answer}
                </p>
              </div>
            </div>
            {turn.status === "answered" &&
              turn.sections.map(([label, lines]) => (
                <div key={label} className={styles.answerSection}>
                  <div className={styles.answerLabel}>{label}</div>
                  {lines.map((line) => (
                    <div key={line} className={styles.answerLine}>
                      {line}
                    </div>
                  ))}
                </div>
              ))}
          </div>
        ))}
        {composing && (
          <form
            className={styles.composer}
            onSubmit={(event) => {
              event.preventDefault();
              const text = draft.trim();
              if (!text) return;
              setDraft("");
              setComposing(false);
              void ask(text);
            }}
          >
            <label htmlFor="question" className="sr-only">
              Your question
            </label>
            <textarea
              id="question"
              value={draft}
              maxLength={500}
              rows={3}
              autoFocus
              placeholder={`Ask about Lap ${target?.lap ?? ""}…`}
              onChange={(event) => setDraft(event.target.value)}
            />
            <div className={styles.composerActions}>
              <Button variant="muted" onClick={() => setComposing(false)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" disabled={!draft.trim()}>
                Ask
              </Button>
            </div>
          </form>
        )}
      </div>
      {last && last.status !== "pending" && (
        <div className={styles.questionFooter}>
          <span className={styles.answered} data-status={last.status}>
            {last.status === "answered" ? (
              <StatusGlyph tone="green" glyph="check" size={14} />
            ) : (
              <StatusGlyph tone="amber" glyph="alert" size={14} />
            )}
            {last.status === "answered" ? "Answered" : "Unavailable"}
          </span>
          {last.status === "answered" && (
            <span className={styles.helpful}>
              Was this helpful?
              <IconButton label="Helpful" aria-pressed={feedback === "up"} className={styles.thumb} onClick={() => setFeedback("up")}>
                {feedback === "up" ? <CheckIcon size={14} /> : <ThumbUpIcon size={14} />}
              </IconButton>
              <IconButton
                label="Not helpful"
                aria-pressed={feedback === "down"}
                className={`${styles.thumb} ${styles.thumbDown}`}
                onClick={() => setFeedback("down")}
              >
                <ThumbDownIcon size={14} />
              </IconButton>
            </span>
          )}
        </div>
      )}
    </Card>
  );
}

