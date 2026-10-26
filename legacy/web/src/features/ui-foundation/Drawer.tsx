"use client";

import { useEffect, useId, useRef, type CSSProperties, type ReactNode, type SyntheticEvent } from "react";
import { CloseIcon } from "./icons";
import { Button, IconButton } from "./primitives";
import styles from "./primitives-extra.module.css";

/**
 * Shows the native <dialog> as a modal while mounted (focus trap, inert page
 * and Escape come from the platform) and returns focus to whatever had it
 * when the dialog opened.
 */
function useModalDialog() {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  return ref;
}

export type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Panel width in px; defaults to 420. */
  width?: number;
  children: ReactNode;
  footer?: ReactNode;
};

/** Right-hand side panel. Mounted only while `open`; Escape and backdrop click call `onClose`. */
export function Drawer({ open, ...rest }: DrawerProps) {
  return open ? <DrawerDialog {...rest} /> : null;
}

function DrawerDialog({ onClose, title, width = 420, children, footer }: Omit<DrawerProps, "open">) {
  const ref = useModalDialog();
  const titleId = useId();
  return (
    <dialog
      ref={ref}
      className={styles.drawer}
      aria-labelledby={titleId}
      style={{ "--drawer-width": `${width}px` } as CSSProperties}
      onCancel={(event: SyntheticEvent) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className={styles.drawerPanel}>
        <header className={styles.drawerHead}>
          <h2 className={styles.drawerTitle} id={titleId}>
            {title}
          </h2>
          <IconButton label="Close" onClick={onClose}>
            <CloseIcon size={14} />
          </IconButton>
        </header>
        <div className={styles.drawerBody}>{children}</div>
        {footer && <footer className={styles.drawerFooter}>{footer}</footer>}
      </div>
    </dialog>
  );
}

export type ConfirmDialogProps = {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "default";
  onConfirm: () => void;
  onCancel: () => void;
};

/** Small centred dialog for destructive or expensive confirmations. */
export function ConfirmDialog({ open, ...rest }: ConfirmDialogProps) {
  return open ? <ConfirmDialogBody {...rest} /> : null;
}

function ConfirmDialogBody({
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "default",
  onConfirm,
  onCancel,
}: Omit<ConfirmDialogProps, "open">) {
  const ref = useModalDialog();
  const titleId = useId();
  const bodyId = useId();
  return (
    <dialog
      ref={ref}
      className={styles.confirm}
      role="alertdialog"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event: SyntheticEvent) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <div className={styles.confirmPanel}>
        <h2 className={styles.confirmTitle} id={titleId}>
          {title}
        </h2>
        <div className={styles.confirmBody} id={bodyId}>
          {body}
        </div>
        <div className={styles.confirmActions}>
          <Button onClick={onCancel}>{cancelLabel}</Button>
          <Button variant={tone === "danger" ? "primary" : "outline-blue"} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
