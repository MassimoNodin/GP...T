import type { Metadata } from "next";
import { ENGINE_OFFLINE_FOOTER } from "@/features/live-status/map";
import { loadSettings } from "@/features/settings/load";
import { parseSection } from "@/features/settings/sections";
import { Settings } from "@/features/settings/Settings";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Settings · Race Engineer" };

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const [model, query] = await Promise.all([loadSettings(), searchParams]);
  return (
    <AppShell
      active="settings"
      live="engineer"
      status={model.pill}
      context={<HeaderContext items={model.header.items} />}
      footer={
        model.engineReachable
          ? { tone: model.pill.tone, label: model.pill.label, lines: model.footerLines }
          : ENGINE_OFFLINE_FOOTER
      }
    >
      <Settings model={model} initialSection={parseSection(query.section)} />
    </AppShell>
  );
}
