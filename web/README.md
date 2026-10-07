# Historical dashboard

## API-fed test data

Click **Test data** in the header, or open `/settings?test_data=populated`, to populate all eight pages from `GET /api/test-data`. Choose populated, empty, partial, stale or unavailable scenarios; select synthetic sessions/laps and play, pause, step or reset the feed. The Next API works without the Python service or game. Synthetic data is labelled and isolated from real recordings and preferences. See [usage and contract](../docs/api-test-data.md).

This Next.js app renders the local F1 Engineer archive and calls the FastAPI
service from the server. The API binds to loopback; its UDP telemetry listener
uses the configured bind address and defaults to `0.0.0.0:20777`. The database
path and recording root are configured only on the API process.

From the repository root, start the API:

```powershell
uv sync --extra app
uv run --extra app f1-engineer api --database data/f1-engineer.sqlite3 --recordings-root recordings --control-token-file data/.f1-engineer-control-token
```

Then start the dashboard in a second terminal:

```powershell
cd web
npm ci
npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). Use the recording panel to
start and stop UDP capture. Finalized `.f1ecap` files appear in the inbox; import
them explicitly for historical analysis. The API creates the local control
token file; the Next server reads it for same-origin recording and import
actions. The token is not sent to the browser. For data import, supported
analysis, and privacy details, see the repository [README](../README.md).

The dashboard loads a model catalog from `GET /api/v1/track-models`. Select a
registered ID and revision in the comparison form to include local diagnostic
distance-region evidence. The browser sends the ID and revision, never a model
filesystem path. The current draft Melbourne regions are not verified circuit
corners and are available only for Time Trial comparisons.
