# Career race diagnostics

The v2 provider service builds a bounded, descriptive report for one published
race lap:

```python
from f1_engineer.analysis.race_diagnostics import build_session_race_diagnostics

report = build_session_race_diagnostics(provider, session_id, revision_id)
```

The provider must implement `evidence(revision_id, session=session_id)`,
returning `(attempt_metadata, row_dicts)` in the v2 evidence shape. The service
returns an `available` or `unavailable` report. `reason` is always a string or
`None`; `reasons` lists all stable rejection codes. It checks published
readiness, player role, verified binding, manifest epoch/format/qualifications
and frame bounds, row identity/counts, race context, and trace continuity.

Race session type IDs follow the repository packet decoder: 15 (`race`), 16
(`race_2`), and 17 (`race_3`). Other session types are rejected. Available
reports contain only lap metadata and aggregate distance/speed ranges, not a
sample projection. Missing fuel, tyre, traffic, and stint observations appear
both as unknown context and explicit `qualifications` entries. Tyre compounds
are categorical IDs with observation counts; no numeric ordering is implied.
Unavailable or negative fuel values do not contribute. An explicit
`car_status_available=False` suppresses fuel and tyre values from that row.

The report is diagnostic only; it provides no coaching, ranking, or strategy
recommendations and does not change reference eligibility.

Frame counters must move forward, but do not need to advance by exactly one
between retained observations. Sparse transitions are counted and qualified as
`nonconsecutive_observation_frames`, with no interpolation or continuity claim.
Duplicate/backward counters, regressing or excessive time gaps, missing retained
rows, acquisition-gap epochs, and manifest qualifications still reject the lap.
