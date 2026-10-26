import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ExternalToolMenu } from "@/features/lap-analysis/ExternalToolMenu";
import { LapAnalysis } from "@/features/lap-analysis/LapAnalysis";
import { loadLapAnalysis } from "@/features/lap-analysis/load";
import { extendLapAnalysis } from "@/features/lap-analysis/load-extras";
import { parseInitialState } from "@/features/lap-analysis/url-state";
import { liveShellProps, loadShellState } from "@/features/live-status/shell-state";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Lap Analysis · Race Engineer" };

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LapAnalysisPage({ params, searchParams }: PageProps<"/review/[session]/laps">) {
  const { session } = await params;
  const query = await searchParams;
  const [base, state] = await Promise.all([
    loadLapAnalysis(decodeURIComponent(session), first(query.target), first(query.reference)),
    loadShellState(),
  ]);
  if (!base) notFound();
  const model = await extendLapAnalysis(base);
  const shell = model.live ? liveShellProps(state, "telemetry", model.footer) : { status: model.status, footer: model.footer };
  const target = model.laps.find((lap) => lap.id === model.targetId);
  const reference = model.laps.find((lap) => lap.id === model.referenceId);
  const compared = target
    ? reference && reference.id !== target.id
      ? `Lap ${target.lap} vs ${reference.lap}`
      : `Lap ${target.lap}`
    : "No lap";
  return (
    <AppShell
      active="review"
      live={model.live ? "telemetry" : undefined}
      status={shell.status}
      context={
        <HeaderContext
          items={[...model.header, compared]}
          action={<ExternalToolMenu laps={model.laps} fileName={model.track} />}
        />
      }
      footer={shell.footer}
    >
      <LapAnalysis model={model} initial={parseInitialState(query)} />
    </AppShell>
  );
}
