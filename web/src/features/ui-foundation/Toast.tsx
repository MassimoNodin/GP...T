"use client";

import Link from "next/link";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { CloseIcon, StatusGlyph } from "./icons";
import { IconButton } from "./primitives";
import styles from "./primitives-extra.module.css";

export type ToastInput = {
  tone: "success" | "info" | "warning" | "error";
  title: string;
  body?: string;
  action?: { label: string; href?: string; onClick?: () => void };
  /** Auto-dismiss delay. Defaults to 5 s (errors persist); 0 or less persists. */
  durationMs?: number;
};

type ToastEntry = ToastInput & { id: string };

export type ToastApi = {
  push(toast: ToastInput): string;
  dismiss(id: string): void;
};

export const MAX_VISIBLE_TOASTS = 3;
export const DEFAULT_TOAST_MS = 5000;

/** Auto-dismiss delay for a toast, or null when it stays until dismissed. */
export function toastDuration(toast: Pick<ToastInput, "tone" | "durationMs">): number | null {
  if (toast.durationMs !== undefined) return toast.durationMs > 0 ? toast.durationMs : null;
  return toast.tone === "error" ? null : DEFAULT_TOAST_MS;
}

/** Appends a toast and drops the oldest ones beyond the visible limit. */
export function addToast(list: ToastEntry[], toast: ToastEntry, max = MAX_VISIBLE_TOASTS): ToastEntry[] {
  return [...list, toast].slice(-max);
}

const NOOP_API: ToastApi = { push: () => "", dismiss: () => {} };
const ToastContext = createContext<ToastApi>(NOOP_API);

/** Push toasts from any client component under `ToastProvider` (mounted in AppShell). */
export function useToast(): ToastApi {
  return useContext(ToastContext);
}

const GLYPH = {
  success: { tone: "green", glyph: "check" },
  info: { tone: "blue", glyph: "info" },
  warning: { tone: "amber", glyph: "alert" },
  error: { tone: "red", glyph: "alert" },
} as const;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const counter = useRef(0);

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback((toast: ToastInput) => {
    counter.current += 1;
    const id = `toast-${counter.current}`;
    setToasts((current) => addToast(current, { ...toast, id }));
    return id;
  }, []);

  const api = useMemo(() => ({ push, dismiss }), [push, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <section className={styles.toastRegion} aria-label="Notifications">
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </section>
    </ToastContext.Provider>
  );
}

export function ToastCard({ toast, onDismiss }: { toast: ToastEntry; onDismiss: (id: string) => void }) {
  const duration = toastDuration(toast);
  const remaining = useRef(duration);
  const startedAt = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused || remaining.current === null) return;
    startedAt.current = Date.now();
    timer.current = setTimeout(() => onDismiss(toast.id), remaining.current);
    return () => {
      if (timer.current) clearTimeout(timer.current);
      if (remaining.current !== null) {
        remaining.current = Math.max(0, remaining.current - (Date.now() - startedAt.current));
      }
    };
  }, [paused, onDismiss, toast.id]);

  const glyph = GLYPH[toast.tone];
  const action = toast.action;
  return (
    <div
      className={styles.toast}
      data-tone={toast.tone}
      role={toast.tone === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <span className={styles.toastGlyph}>
        <StatusGlyph tone={glyph.tone} glyph={glyph.glyph} size={16} />
      </span>
      <div className={styles.toastText}>
        <strong className={styles.toastTitle}>{toast.title}</strong>
        {toast.body && <p className={styles.toastBody}>{toast.body}</p>}
        {action &&
          (action.href ? (
            <Link href={action.href} className={styles.toastAction} onClick={() => onDismiss(toast.id)}>
              {action.label}
            </Link>
          ) : (
            <button
              type="button"
              className={styles.toastAction}
              onClick={() => {
                action.onClick?.();
                onDismiss(toast.id);
              }}
            >
              {action.label}
            </button>
          ))}
      </div>
      <IconButton label="Dismiss notification" className={styles.toastDismiss} onClick={() => onDismiss(toast.id)}>
        <CloseIcon size={13} />
      </IconButton>
    </div>
  );
}
