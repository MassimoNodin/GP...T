# API-fed UI test data

## Open the test workspace

Click **Test data** in the application header, or open `/settings?test_data=populated`. All eight mock pages have a synthetic workspace using the same sidebar and shared map/trace presentation components. The yellow controls and header identify the data as synthetic.

The workspace fetches **`GET /api/test-data`** from the Next web application. It works without the Python service, a game, a capture, Ollama or a microphone. It creates no database records, recording files or persisted preferences.

Use the sidebar to move between Dashboard, Live Telemetry, Sessions, Lap Comparison, Track Analysis, AI Engineer, Recordings and Settings. Select a session, target lap and reference lap; those selections carry across pages in bounded `test_session`, `test_target` and `test_reference` URL keys. Choosing a new session resets its laps to the API defaults. Only this synthetic state is carried in test mode; production operation/evidence identities are removed.

**Exit test mode** returns to the corresponding real-data page. **Reset test data** clears synthetic selections and returns its clock to zero.

## Scenarios and controls

| Scenario | Supplied information |
|---|---|
| `populated` | Monza, Zandvoort and Spa examples; twelve laps per session; named turns; five telemetry channels; lap delta; capture counters/catalog; import progress; lifecycle/inventory; Engineer example; configuration/storage examples. |
| `empty` | No selected sessions, laps, captures, telemetry or analysis. Panels render empty states. |
| `partial` | Deliberate geometry/signal gaps and absent fuel, ERS, temperatures, tyre and inventory fields. Unsupported regions have no timing classification. |
| `stale` | An aged telemetry snapshot. The live gauge and car values are hidden; snapshot age stays visible. |
| `unavailable` | HTTP 503 with an unavailable envelope. Prior values clear and the page offers retry. |

**Play test feed** advances after each successful API response and a one-second delay. **Pause test feed** freezes the simulated clock. **Step test feed** requests the next tick. A failed request stops playback and clears displayed data; there is no fallback to examples or real services. The clock is bounded to 0–3600 and starts at zero on each page navigation. Selection changes remount region/cursor state. Maps, region selections, chart channels, cursor and zoom use the supplied API samples.

Test settings are read-only examples. Engineer messages are deterministic fixture output; they do not call the local model or imply coaching. Replay is synthetic telemetry/path presentation and contains no game video. Synthetic budgets and disk sizes are not measurements of this computer. This feature supplies UI data; the remaining-page parity audit still identifies production integration and layout work.

## API contract

```text
GET /api/test-data?scenario=populated&session=test-session-spa&target=test-lap-12&reference=test-lap-8&tick=10
```

Optional query parameters:

- `scenario`: `populated` (default), `empty`, `partial`, `stale`, `unavailable`.
- `session`: `test-session-monza` (default), `test-session-zandvoort`, `test-session-spa`.
- `target`: `test-lap-1` … `test-lap-12`; default `test-lap-12`.
- `reference`: the same lap catalog; default `test-lap-8`.
- `tick`: integer 0–3600; default 0.

Unknown/repeated parameters, oversized values, unknown selections and invalid ticks return HTTP 422 with no data. GET is the only implemented operation. Responses use `Cache-Control: no-store`, `X-F1-Data-Source: synthetic` and the existing `ApiResponse` envelope. Successful data includes `schema_version: 1`, `source.kind: synthetic`, fixture version `ui-test-v1`, exact selected IDs/tick, deterministic UTC timestamp and `AnalysisInput` version 1. `diagnostic_only` is true; `coaching_eligible` and `ranking_eligible` are false. The same request yields the same data.

The fixture generator is server-side in `web/src/lib/test-data-fixtures.ts`. Modify it to change examples; the browser obtains all sample values over the API. The typed UI model is separate from production evidence contracts and its synthetic IDs cannot resolve to real controller IDs.

## Implementation review

The required `gpt-6.1-sol` architecture/code review approved the separate API namespace and early page branches. Its findings led to response-paced playback, bounded synthetic URL selection, consistent processed lifecycle examples and v1 envelope checks. See [Decision 0090](architecture.md#decision-0090-populate-pages-through-an-isolated-synthetic-api).
