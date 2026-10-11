import type { Metadata } from "next";
import { loadSettings } from "@/features/settings/load";
import { Settings } from "@/features/settings/Settings";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Settings · Race Engineer" };

export default async function SettingsPage() {
  const model = await loadSettings();
  return (
    <AppShell
      active="settings"
      live="engineer"
      status={model.pill}
      context={<HeaderContext items={model.header.items} />}
      footer={{ tone: model.pill.tone, label: model.pill.label, lines: model.footerLines }}
    >
      <Settings model={model} />
    </AppShell>
  );
}
