import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { loadOverview } from "@/features/session-overview/load";
import { parseOverviewParams } from "@/features/session-overview/overview-data";
import { SessionOverview } from "@/features/session-overview/SessionOverview";
import { liveShellProps, loadShellState } from "@/features/live-status/shell-state";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Session Overview · Race Engineer" };

export default async function SessionOverviewPage({
  params,
  searchParams,
}: PageProps<"/review/[session]">) {
  const { session } = await params;
  const [model, state] = await Promise.all([loadOverview(decodeURIComponent(session)), loadShellState()]);
  if (!model) notFound();
  const shell = model.shell.live ? liveShellProps(state, "telemetry", model.footer) : { status: model.shell.status, footer: model.footer };
  const initial = parseOverviewParams(await searchParams, model.laps, { selectedId: model.selectedId });
  return (
    <AppShell
      active="review"
      live={model.shell.live ? "telemetry" : undefined}
      status={shell.status}
      context={<HeaderContext items={model.header} />}
      footer={shell.footer}
    >
      <SessionOverview model={model} initial={initial} />
    </AppShell>
  );
}
