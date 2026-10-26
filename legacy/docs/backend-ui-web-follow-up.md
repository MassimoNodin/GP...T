# Backend/UI follow-up

October 12, 2026. Planning only; this backend continuation does not change `web/`.

## Available read endpoints

| Endpoint | UI consumer | Handling |
| --- | --- | --- |
| `GET /api/v2/session-evidence/sessions/{id}/laps` | Overview laps/stints, Lap Analysis sectors | Keep unknown sectors/tyres explicit; preserve per-attempt revision identity and scan limitations. |
| `GET /api/v2/session-evidence/sessions/{id}/summary` | Overview timeline, Library dates | Show observed capture times as such; do not infer UTC times from game session time. Display partial-scan status and omitted-event counts. |
| `GET /api/v1/track-models/{model_id}/regions?revision=...` | Lap Analysis region overlays | Pass an exact catalog revision; label drafts and never imply validated corner identity. |
| `GET /api/v2/session-evidence/sessions/{id}/track-models` | Session-compatible model selection | Empty compatible model list is normal. Do not substitute another circuit, format or track length. |
| `GET /api/v2/engineer/events?after=...&limit=...&session_id=...` | Drive radio history | Use returned `next_after`; avoid duplicate IDs. Paginate with bounded limits and filter session-specific views. |
| `GET /api/v1/engineer/preferences` | Live/Review Assistance settings | Preserve the returned revision/ETag, strict complete schema, and unreadable-file errors. |

## Mutations and explicit evidence requests

- Proxy `PUT /api/v1/engineer/preferences` with the server-held control token;
  send the complete preference document and `If-Match` revision. Surface conflicts
  rather than silently overwriting concurrent changes. Never expose the token in
  browser JavaScript or browser-local storage.
- Wire recorded-session questions to the existing v2 ask endpoint using either an
  exact target revision or an explicitly chosen future binding, never both. Model
  use and historical materialization are independent explicit opt-ins.
- Keep deferred/unprocessed evidence distinct from complete trace availability.
  Processing buttons must explicitly request materialization/analyze, respect busy
  and unavailable responses, and never create bulk background replay.
- Future-binding questions can last up to 270 seconds; configure proxy deadlines
  accordingly and propagate cancellation. Ordinary requests retain their existing
  30-second service deadline.
- Add import-progress wiring without changing import admission or receiver tuning.

## Remaining backend/model limitations

Jeddah and Sakhir draft catalog artifacts still need suitable authoring sources.
Region lookup alone does not add circuit geometry, a track-model racing line,
reviewed corner names, coaching eligibility, or competitive ranking.

Deterministic activity calls currently observe telemetry health and published
player attempts; pit-related messages are lap-result observations, not a
packet-time pit-entry notification guarantee. The preferences endpoint does not
implement browser audio playback, interruption, or speech queuing.

Do not call the potentially expensive summary scan once for every Library row.
Load summaries on demand for selected sessions until a cheap date projection is
implemented. Preserve unavailable placeholders where data is absent.

## Acceptance before web implementation

Approve web integration separately. Test proxy auth, revision conflicts, missing
data, cursor polling, session switches, draft warnings, partial scans and explicit
processing opt-ins. Verify UI with synthetic/disposable data first; production
deployment and real-game/Linux release gates require their own authorization.
