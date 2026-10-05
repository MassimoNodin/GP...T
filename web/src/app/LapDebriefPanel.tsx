import type { Comparison, LapRecord } from "@/lib/api";
import { lapDebriefMatchesComparison } from "@/lib/lap-debrief-match";
import { buildRecordedSpeechPlan } from "@/lib/recorded-speech-plan";
import RecordedEvidenceSpeech from "./RecordedEvidenceSpeech";

export default function LapDebriefPanel({
  comparison,
  target,
  reference,
}: {
  comparison: Comparison;
  target: LapRecord;
  reference: LapRecord;
}) {
  const value: unknown = comparison.lap_debrief;
  if (value == null) return null;
  if (!lapDebriefMatchesComparison(value, comparison, target, reference)) {
    return (
      <section
        className="lap-debrief panel"
        role="status"
        aria-label="Recorded lap debrief unavailable"
      >
        <div className="comparison-brief-heading">
          <div>
            <span className="eyebrow">RECORDED LAP DEBRIEF</span>
            <h3>Debrief not shown</h3>
          </div>
        </div>
        <p className="comparison-brief-text">
          The debrief did not match the selected attempts and comparison
          evidence, so it was hidden. The comparison charts remain available.
        </p>
      </section>
    );
  }

  const debrief = value;
  return (
    <section className="lap-debrief panel" aria-label="Recorded lap debrief">
      <div className="comparison-brief-heading">
        <div>
          <span className="eyebrow">RECORDED LAP DEBRIEF</span>
          <h3>Lap result at a glance</h3>
        </div>
        <span className="brief-version">
          {debrief.status} · {debrief.analysis_version}
        </span>
      </div>
      <p className="comparison-brief-text">{debrief.text}</p>
      {debrief.ranked_regions.length > 0 ? (
        <ol className="lap-debrief-regions">
          {debrief.ranked_regions.map((region) => (
            <li key={`${region.rank}-${region.region_id}`}>
              <strong>{region.text}</strong>
              <small>
                Rank {region.rank} · {region.provenance.target?.attempt_key} vs{" "}
                {region.provenance.reference?.attempt_key}
                {region.provenance.model?.model_id
                  ? ` · ${region.provenance.model.model_id} revision ${region.provenance.model.revision}`
                  : ""}
              </small>
            </li>
          ))}
        </ol>
      ) : null}
      {debrief.omitted_region_count > 0 ? (
        <p className="compare-report-scope" role="note">
          {debrief.omitted_region_count} additional ranked regions were omitted
          by the report limits.
        </p>
      ) : null}
      {debrief.limitations.length > 0 ? (
        <ul className="brief-limitations lap-debrief-limitations">
          {debrief.limitations.map((item) => (
            <li key={item.code}>{item.text}</li>
          ))}
        </ul>
      ) : null}
      {debrief.omitted_limitation_count > 0 ? (
        <p className="compare-report-scope" role="note">
          {debrief.omitted_limitation_count} additional limitation details were
          omitted by the report limits.
        </p>
      ) : null}
      <RecordedEvidenceSpeech
        planResult={buildRecordedSpeechPlan({
          kind: "lap_debrief",
          report: debrief,
          targetAttemptKey: target.attempt_key,
          referenceAttemptKey: reference.attempt_key,
        })}
      />
    </section>
  );
}
