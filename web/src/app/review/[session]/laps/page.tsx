import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ExportLapsButton } from "@/features/lap-analysis/ExportLapsButton";
import { LapAnalysis } from "@/features/lap-analysis/LapAnalysis";
import { loadLapAnalysis } from "@/features/lap-analysis/load";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Lap Analysis · Race Engineer" };

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LapAnalysisPage({ params, searchParams }: PageProps<"/review/[session]/laps">) {
  const { session } = await params;
  const query = await searchParams;
  const model = await loadLapAnalysis(decodeURIComponent(session), first(query.target), first(query.reference));
  if (!model) notFound();
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
      status={model.status}
      context={
        <HeaderContext
          items={[...model.header, compared]}
          action={<ExportLapsButton laps={model.laps} fileName={`${model.track.toLowerCase().replace(/\W+/g, "-")}-laps`} />}
        />
      }
      footer={model.footer}
    >
      <LapAnalysis model={model} />
    </AppShell>
  );
}
