"use client";

import { useState } from "react";
import { parseRegionsResponse, type RegionModel } from "@/features/review-data/regions";
import { humanReason } from "@/features/review-data/reasons";
import { DataTable, type Column } from "@/features/ui-foundation/DataTable";
import { Bone } from "@/features/route-states/Skeleton";
import { Drawer } from "@/features/ui-foundation/Drawer";
import { Badge, Button, Card } from "@/features/ui-foundation/primitives";
import { EmptyState, InlineAlert, NotAvailable } from "@/features/ui-foundation/states";
import {
  DRAFT_FOOTNOTE,
  regionRows,
  regionsHeading,
  type RegionRow,
  type TrackModelRow,
  type TrackModelsView,
} from "./track-models";
import styles from "./Settings.module.css";
import tabStyles from "./SettingsTabs.module.css";

type RegionsState =
  | { status: "loading" }
  | { status: "error"; text: string }
  | { status: "ready"; model: RegionModel };

async function fetchRegionModel(row: TrackModelRow): Promise<RegionsState> {
  try {
    const response = await fetch(
      `/api/track-models/${encodeURIComponent(row.modelId)}/regions?revision=${row.revision}`,
      { cache: "no-store" },
    );
    const body = (await response.json().catch(() => null)) as { reason?: unknown } | null;
    if (!response.ok) {
      const reason = typeof body?.reason === "string" ? body.reason : null;
      return { status: "error", text: humanReason(reason) || "The regions could not be read." };
    }
    const model = parseRegionsResponse(body);
    return model
      ? { status: "ready", model }
      : { status: "error", text: "The engine sent regions this app could not read." };
  } catch {
    return { status: "error", text: "The local engine could not be reached." };
  }
}

const REGION_COLUMNS: Column<RegionRow>[] = [
  { key: "label", header: "Region", width: "minmax(90px, 1fr)", render: (row) => row.label },
  { key: "range", header: "Range", width: "150px", render: (row) => row.range },
  {
    key: "apex",
    header: "Apex",
    width: "76px",
    numeric: true,
    render: (row) => row.apex ?? <NotAvailable reason="No apex distance is recorded for this region" compact />,
  },
  {
    key: "direction",
    header: "Direction",
    width: "84px",
    render: (row) => row.direction ?? <NotAvailable reason="No direction is recorded for this region" compact />,
  },
];

function TrackModelsCard({ view }: { view: TrackModelsView }) {
  const [open, setOpen] = useState<TrackModelRow | null>(null);
  const [regions, setRegions] = useState<RegionsState>({ status: "loading" });

  function show(row: TrackModelRow) {
    setOpen(row);
    setRegions({ status: "loading" });
    void fetchRegionModel(row).then((next) => {
      setRegions((current) => (current.status === "loading" ? next : current));
    });
  }

  const columns: Column<TrackModelRow>[] = [
    { key: "circuit", header: "Circuit", width: "minmax(120px, 1fr)", render: (row) => row.circuit },
    { key: "model", header: "Model", width: "130px", render: (row) => row.modelId },
    { key: "revision", header: "Revision", width: "80px", numeric: true, render: (row) => row.revision },
    { key: "regions", header: "Regions", width: "76px", numeric: true, render: (row) => row.regionCount },
    {
      key: "status",
      header: "Status",
      width: "92px",
      footnote: DRAFT_FOOTNOTE,
      render: (row) => <Badge tone={row.reviewed ? "green" : "amber"}>{row.statusLabel}</Badge>,
    },
    { key: "origin", header: "Origin", width: "120px", render: (row) => row.originLabel },
    {
      key: "length",
      header: "Length",
      width: "84px",
      numeric: true,
      render: (row) => row.lengthLabel ?? <NotAvailable reason="The model does not state a track length" compact />,
    },
    {
      key: "action",
      header: "Details",
      width: "130px",
      align: "end",
      render: (row) => (
        <Button className={tabStyles.small} onClick={() => show(row)} aria-label={`View regions for ${row.circuit} revision ${row.revision}`}>
          View regions
        </Button>
      ),
    },
  ];

  return (
    <Card title="Track Models" labelledBy="track-models">
      <p className={styles.cardDesc}>Circuit models used for regions and corner analysis.</p>
      {view.state === "ok" ? (
        <DataTable
          caption="Installed track models"
          columns={columns}
          rows={view.rows}
          rowKey={(row) => row.key}
          density="compact"
          stickyHeader
          maxHeight={360}
          empty={<EmptyState size="inline" title="No track models are installed." />}
        />
      ) : view.state === "unavailable" ? (
        <InlineAlert tone="warning">
          The track model list could not be read ({humanReason(view.reason) || "the engine reported a problem"}).
        </InlineAlert>
      ) : (
        <EmptyState
          size="inline"
          tone="warning"
          title="Track models cannot be listed because the local engine is not reachable."
        />
      )}
      {view.state === "ok" && view.rows.length > 0 && <p className={styles.footnote}>{DRAFT_FOOTNOTE}</p>}

      <Drawer
        open={open !== null}
        onClose={() => setOpen(null)}
        title={open ? `Regions · ${open.circuit}` : "Regions"}
        width={480}
      >
        {regions.status === "loading" && (
          <div role="status" aria-label="Loading regions">
            {[0, 1, 2, 3, 4].map((line) => (
              <div key={line} className={tabStyles.mt}>
                <Bone width="80%" height={14} />
              </div>
            ))}
          </div>
        )}
        {regions.status === "error" && <InlineAlert tone="error">{regions.text}</InlineAlert>}
        {regions.status === "ready" && (
          <>
            <p className={tabStyles.drawerHeading}>{regionsHeading(regions.model)}</p>
            {regions.model.validation !== "validated" && (
              <div className={tabStyles.banner}>
                <InlineAlert tone="info">{DRAFT_FOOTNOTE}</InlineAlert>
              </div>
            )}
            <DataTable
              caption={`Regions of ${regions.model.trackName}`}
              columns={REGION_COLUMNS}
              rows={regionRows(regions.model.regions)}
              rowKey={(row) => row.id}
              density="compact"
              empty={<EmptyState size="inline" title="This model has no regions." />}
            />
          </>
        )}
      </Drawer>
    </Card>
  );
}

export function AdvancedPanel({ trackModels }: { trackModels: TrackModelsView }) {
  return (
    <div className={styles.grid}>
      <div className={tabStyles.span2}>
        <TrackModelsCard view={trackModels} />
      </div>
      <div className={tabStyles.span2}>
        <Card title="Diagnostic Tools" labelledBy="diagnostic-tools">
          <p className={styles.cardDesc}>Replay and capture tools run from the command line so they never compete with the live receiver.</p>
          <dl className={styles.kv}>
            <div>
              <dt>Check a capture</dt>
              <dd>
                <code>f1-engineer inspect &lt;capture.f1ecap&gt;</code>
              </dd>
            </div>
            <div>
              <dt>Validate a replay</dt>
              <dd>
                <code>f1-engineer replay-udp &lt;capture.f1ecap&gt; --host &lt;receiver-ip&gt; --dry-run</code>
                <span className={styles.cmdNote}>
                  Sends nothing. Use <code>--confirm-idle</code> instead of <code>--dry-run</code> to send, with F1 off.
                </span>
              </dd>
            </div>
            <div>
              <dt>Manual acquisition</dt>
              <dd>
                <code>f1-engineer api --manual-acquisition</code>
                <span className={styles.cmdNote}>
                  For legacy manual capture diagnostics only. It turns off automatic acquisition, so sessions are not
                  recorded from the game.
                </span>
              </dd>
            </div>
            <div>
              <dt>Receiver address</dt>
              <dd>
                <code>f1-engineer api --udp-host &lt;ip&gt; --udp-port &lt;port&gt;</code>
              </dd>
            </div>
          </dl>
          <p className={styles.footnote}>
            Run these with <code>uv run --extra app</code> or from the project&apos;s virtual environment. See README.md for details.
          </p>
        </Card>
      </div>
    </div>
  );
}
