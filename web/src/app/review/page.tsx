import type { Metadata } from "next";
import { loadLibrary } from "@/features/session-library/load";
import { SessionLibrary } from "@/features/session-library/SessionLibrary";
import { AppShell, HeaderContext } from "@/features/ui-foundation/AppShell";

export const metadata: Metadata = { title: "Session Library · Race Engineer" };

export default async function ReviewPage() {
  const model = await loadLibrary();
  return (
    <AppShell
      active="review"
      status={model.status}
      context={<HeaderContext items={model.header.items} trailing={model.header.trailing} />}
      footer={model.footer}
    >
      <SessionLibrary model={model} />
    </AppShell>
  );
}
