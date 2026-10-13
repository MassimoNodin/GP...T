"use client";

import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { UploadIcon } from "./icons";
import styles from "./primitives-extra.module.css";

export type DropRejection = "type" | "count";

export type DropzoneProps = {
  /** Allowed file extensions (".f1ecap") or MIME types ("audio/wav"). */
  accept: string[];
  maxFiles: number;
  onFiles: (files: File[]) => void;
  onReject: (reason: DropRejection) => void;
  children: ReactNode;
  /** Text of the overlay shown while files are dragged over the region. */
  overlayLabel: string;
  disabled?: boolean;
};

function matchesAccept(file: { name: string; type?: string }, accept: string[]) {
  const name = file.name.toLowerCase();
  const type = (file.type ?? "").toLowerCase();
  return accept.some((rule) => {
    const wanted = rule.toLowerCase();
    if (wanted.startsWith(".")) return name.endsWith(wanted);
    if (wanted.endsWith("/*")) return type.startsWith(wanted.slice(0, -1));
    return type === wanted;
  });
}

/**
 * Pure drop validation: any file of an unaccepted type rejects the whole drop
 * ("type"), then more files than `maxFiles` rejects it ("count"). Nothing is
 * partially accepted.
 */
export function classifyDrop<T extends { name: string; type?: string }>(
  files: T[],
  accept: string[],
  maxFiles: number,
): { ok: true; files: T[] } | { ok: false; reason: DropRejection } {
  if (files.length === 0) return { ok: false, reason: "type" };
  if (files.some((file) => !matchesAccept(file, accept))) return { ok: false, reason: "type" };
  if (files.length > maxFiles) return { ok: false, reason: "count" };
  return { ok: true, files };
}

function hasFiles(event: DragEvent) {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

/**
 * Wraps a region and shows a drop overlay while files are dragged over it.
 * Drag and drop is pointer-only: the keyboard path is always the file picker
 * button, so the region itself is not focusable.
 */
export function Dropzone({
  accept,
  maxFiles,
  onFiles,
  onReject,
  children,
  overlayLabel,
  disabled,
}: DropzoneProps) {
  const [active, setActive] = useState(false);
  const depth = useRef(0);

  function reset() {
    depth.current = 0;
    setActive(false);
  }

  return (
    <div
      className={styles.dropzone}
      onDragEnter={(event) => {
        if (disabled || !hasFiles(event)) return;
        event.preventDefault();
        depth.current += 1;
        setActive(true);
      }}
      onDragOver={(event) => {
        if (disabled || !hasFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => {
        if (disabled || !hasFiles(event)) return;
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setActive(false);
      }}
      onDrop={(event) => {
        if (disabled || !hasFiles(event)) return;
        event.preventDefault();
        reset();
        const result = classifyDrop(Array.from(event.dataTransfer.files), accept, maxFiles);
        if (result.ok) onFiles(result.files);
        else onReject(result.reason);
      }}
    >
      {children}
      {active && (
        <div className={styles.dropOverlay} aria-hidden="true">
          <UploadIcon size={28} />
          <span className={styles.dropLabel}>{overlayLabel}</span>
        </div>
      )}
    </div>
  );
}
