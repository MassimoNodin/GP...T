# Historical dashboard

This Next.js app renders the local F1 Engineer archive and calls the FastAPI
service from the server. Both servers bind to loopback; the database path and
recording root are configured only on the API process.

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

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). Copy finished `.f1ecap`
files into `recordings/`, refresh, and start an import from the inbox. The API
creates the local control token file; the Next server reads it for same-origin
import actions. The token is not sent to the browser. For data import, supported
analysis, and privacy details, see the repository [README](../README.md).

The dashboard loads a model catalog from `GET /api/v1/track-models`. Select a
registered ID and revision in the comparison form to include local diagnostic
distance-region evidence. The browser sends the ID and revision, never a model
filesystem path. The current draft Melbourne regions are not verified circuit
corners and are available only for Time Trial comparisons.
