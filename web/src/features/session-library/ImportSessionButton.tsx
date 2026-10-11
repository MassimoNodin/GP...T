"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ChevronDownIcon } from "@/features/ui-foundation/icons";
import styles from "./SessionLibrary.module.css";

type Phase =
  | { kind: "idle" }
  | { kind: "busy"; text: string }
  | { kind: "done"; text: string }
  | { kind: "error"; text: string };

/** Uploads an .f1ecap capture, then queues it for import through the local API. */
export function ImportSessionButton({ enabled }: { enabled: boolean }) {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  async function importFile(file: File) {
    setPhase({ kind: "busy", text: `Uploading ${file.name}…` });
    try {
      const upload = await fetch("/api/recording-sources/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Capture-Upload-Filename": encodeURIComponent(file.name),
        },
        body: file,
      });
      const body = (await upload.json().catch(() => null)) as {
        status?: string;
        data?: { capture_id?: string };
        reason?: string | null;
      } | null;
      const captureId = body?.data?.capture_id;
      if (!upload.ok || body?.status !== "ok" || typeof captureId !== "string") {
        setPhase({ kind: "error", text: `Upload failed${body?.reason ? `: ${body.reason.replaceAll("_", " ")}` : ""}` });
        return;
      }
      setPhase({ kind: "busy", text: "Queuing import…" });
      const form = new FormData();
      form.set("capture_id", captureId);
      form.set("return_to", "recordings");
      form.set("queue_if_busy", "true");
      const queued = await fetch("/api/import-jobs", { method: "POST", body: form });
      const error = new URL(queued.url, window.location.href).searchParams.get("import_error");
      if (!queued.ok || error) {
        setPhase({ kind: "error", text: `Import failed${error ? `: ${error.replaceAll("_", " ")}` : ""}` });
        return;
      }
      setPhase({ kind: "done", text: "Import queued. The session appears here once processed." });
      router.refresh();
    } catch {
      setPhase({ kind: "error", text: "The local engine is unavailable." });
    }
  }

  const busy = phase.kind === "busy";
  return (
    <div className={styles.importWrap}>
      <div className={styles.importButton}>
        <button
          type="button"
          className={styles.importMain}
          disabled={!enabled || busy}
          title={enabled ? undefined : "Start the local engine to import captures"}
          onClick={() => input.current?.click()}
        >
          Import Session
        </button>
        <button
          type="button"
          className={styles.importMenu}
          aria-label="More import options"
          aria-expanded={menuOpen}
          disabled={!enabled || busy}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <ChevronDownIcon size={15} />
        </button>
      </div>
      {menuOpen && (
        <div className={styles.menu} role="menu">
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false);
              input.current?.click();
            }}
          >
            Import capture file (.f1ecap)
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false);
              router.refresh();
            }}
          >
            Refresh library
          </button>
        </div>
      )}
      <input
        ref={input}
        type="file"
        accept=".f1ecap"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void importFile(file);
        }}
      />
      {phase.kind !== "idle" && (
        <p className={styles.importStatus} data-kind={phase.kind} role="status">
          {phase.text}
        </p>
      )}
    </div>
  );
}
