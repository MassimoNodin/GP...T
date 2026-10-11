import Link from "next/link";
import { AppShell, HeaderContext, type NavKey } from "@/features/ui-foundation/AppShell";
import { FileMissingIcon } from "@/features/ui-foundation/icons";
import styles from "./route-states.module.css";

export function RouteNotFound({
  active,
  title,
  message,
  href,
  linkLabel,
}: {
  active: NavKey;
  title: string;
  message: string;
  href: string;
  linkLabel: string;
}) {
  return (
    <AppShell
      active={active}
      status={{ tone: "grey", label: "Not found" }}
      context={<HeaderContext items={["Not found"]} />}
      footer={{ tone: "grey", label: "Not found", lines: [] }}
    >
      <section className={styles.state} aria-labelledby="route-state-title">
        <span className={styles.stateIcon} aria-hidden="true">
          <FileMissingIcon size={24} />
        </span>
        <h1 className={styles.stateTitle} id="route-state-title">
          {title}
        </h1>
        <p className={styles.stateText}>{message}</p>
        <div className={styles.actions}>
          <Link href={href} className={styles.linkButton} data-variant="primary">
            {linkLabel}
          </Link>
        </div>
      </section>
    </AppShell>
  );
}
