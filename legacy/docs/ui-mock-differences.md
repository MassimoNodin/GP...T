# UI mock differences

Approved for recording on October 11, 2026. The supplied five-panel image is
the visual reference, not a promise of implemented features. Ignore the old UI.
Preserve a durable image copy before implementation; the attachment is not a repo asset.

## Required corrections

1. **Standalone engineer:** microphone, questions, proactive messages, and audio
   work without the website. Drive observes it; browser closure cannot stop it.
2. **Replay versus review:** historical inspection is not replay. Replay Mode
   indicates an actual operation, never a requirement for retained review.
3. **Live versus historical:** separate runtime health from the selected historical
   session source, availability, and freshness.
4. **Validity versus quality:** distinguish game validity, telemetry completeness,
   and analysis/comparison eligibility. Complete does not establish all three.
5. **Comparison policy:** identify policy, target/reference, eligibility, and
   incompatibility. Never silently substitute a reference.
6. **Delta arithmetic:** 1:13.420 minus 1:12.842 equals +0.578, matching sector
   totals. Correct unexplained -0.612 values or identify a different baseline.
7. **Source terminology:** acquisition origin, import history, and active replay
   are separate properties. Import/replay does not change original source.

## Requirements not demonstrated

8. **Radio lifecycle:** include interrupted, expired, failed, unsupported, and
   cancelled outcomes where applicable. Runtime-owned history survives browser
   reconnect within an explicit retention policy.
9. **Non-happy states:** account for disconnected, stale, loading, empty, partial,
   and failed states and their consequences for actions and measurements.
10. **Settings application:** confirm success/failure, configured/applied state,
    restart requirements, and browser-local versus persistent runtime configuration.
11. **Data workflows:** include import progress/failure/duplicates, export, actual
    retention behaviour, and explicitly confirmed deletion. Visible buttons and
    unseen settings sections do not establish these workflows.

## Generated content requiring validation

12. **Capabilities:** verify proactive tyre/strategy advice, opponent gaps, steering
    evidence, setup-impact analysis, external tools, and coaching explanations
    against actual support. Never fabricate data to reproduce the image.
13. **Interpretations:** good tyre management, instability, and causes of time
    loss require supporting analysis. Separate observations, interpretation, uncertainty.

## Not differences

- Drive circuit illustration is session identification, not a telemetry dashboard.
- Visual-first overview and contextual Review questions match the vision.
- Primary destinations: Review, Drive Diagnostics, Settings. No separate Dashboard,
  Recordings, Engineer, Compare, Track, or browser HUD is required.

Implementation assignments: [development slices](ui-development-slices.md).
