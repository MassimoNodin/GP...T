import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { loadOverview } from "@/features/session-overview/load";
import { SessionOverview } from "@/features/session-overview/SessionOverview";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Session Overview · Race Engineer" };

export default async function SessionOverviewPage({ params }: PageProps<"/review/[session]">) {
  const { session } = await params;
  const model = await loadOverview(decodeURIComponent(session));
  if (!model) notFound();
  return (
    <AppShell
      active="review"
      live={model.shell.live ? "telemetry" : undefined}
      status={model.shell.status}
      context={<HeaderContext items={model.header} />}
      footer={model.footer}
    >
      <SessionOverview model={model} />
    </AppShell>
  );
}
