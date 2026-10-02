# Historical dashboard

This Next.js app renders the local F1 Engineer archive and calls the read-only
FastAPI service from the server. Both servers bind to loopback; the database
path is configured only on the API process.

From the repository root, start the API:

```powershell
uv sync --extra app
uv run --extra app f1-engineer api --database data/f1-engineer.sqlite3
```

Then start the dashboard in a second terminal:

```powershell
cd web
npm ci
npm run dev
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). For data import,
supported analysis, and privacy details, see the repository [README](../README.md).

The dashboard loads a model catalog from `GET /api/v1/track-models`. Select a
registered ID and revision in the comparison form to include local diagnostic
distance-region evidence. The browser sends the ID and revision, never a model
filesystem path. The current draft Melbourne regions are not verified circuit
corners and are available only for Time Trial comparisons.
