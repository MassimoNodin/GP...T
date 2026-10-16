import type { CSSProperties, ReactNode } from "react";
import styles from "./route-states.module.css";

/** Single shimmering placeholder block (decorative; hidden from assistive tech). */
export function Bone({
  width = "100%",
  height = 14,
  round,
  style,
}: {
  width?: number | string;
  height?: number | string;
  round?: boolean;
  style?: CSSProperties;
}) {
  return (
    <span
      className={styles.skeleton}
      data-round={round ? "true" : undefined}
      style={{ width, height, ...style }}
      aria-hidden="true"
    />
  );
}

export function SkeletonCard({
  rows = 3,
  titleWidth = 140,
  children,
}: {
  rows?: number;
  titleWidth?: number;
  children?: ReactNode;
}) {
  return (
    <div className={styles.card} aria-hidden="true">
      <Bone width={titleWidth} height={15} />
      {children}
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={styles.row}>
          <Bone width={28} height={28} round />
          <div className={styles.rowText}>
            <Bone width={index % 2 ? "58%" : "76%"} height={12} />
            <Bone width={index % 2 ? "34%" : "46%"} height={10} />
          </div>
          <Bone width={64} height={22} />
        </div>
      ))}
    </div>
  );
}

function SkeletonHead({ actions }: { actions?: boolean }) {
  return (
    <div className={styles.head} aria-hidden="true">
      <div className={styles.headText}>
        <Bone width={210} height={22} />
        <Bone width={300} height={13} />
      </div>
      {actions && <Bone width={132} height={34} />}
    </div>
  );
}

function Toolbar({ tabs = 3, extra = 0 }: { tabs?: number; extra?: number }) {
  return (
    <div className={styles.toolbar} aria-hidden="true">
      <Bone width={tabs * 92} height={40} style={{ borderRadius: 9 }} />
      {Array.from({ length: extra }, (_, index) => (
        <Bone key={index} width={150} height={37} />
      ))}
    </div>
  );
}

function ChartCard({ height = 220, title = 160 }: { height?: number; title?: number }) {
  return (
    <div className={styles.card} aria-hidden="true">
      <Bone width={title} height={15} />
      <Bone height={height} />
    </div>
  );
}

export type SkeletonVariant =
  | "drive"
  | "library"
  | "overview"
  | "overview-table"
  | "overview-events"
  | "overview-setup"
  | "laps"
  | "laps-charts"
  | "laps-table"
  | "laps-map"
  | "settings";

/** Rows of a table: a header line and `rows` body lines of `cols` cells. */
function TableCard({ rows = 7, cols = 6, title = 150 }: { rows?: number; cols?: number; title?: number }) {
  const template = { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` };
  return (
    <div className={styles.card} aria-hidden="true">
      <Bone width={title} height={15} />
      <div className={styles.tableRow} style={template}>
        {Array.from({ length: cols }, (_, cell) => (
          <Bone key={cell} width="55%" height={10} />
        ))}
      </div>
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className={styles.tableRow} style={template}>
          {Array.from({ length: cols }, (_, cell) => (
            <Bone key={cell} width={cell === 0 ? "40%" : row % 2 ? "64%" : "78%"} height={12} />
          ))}
        </div>
      ))}
    </div>
  );
}

function HeroCard() {
  return (
    <div className={styles.card} aria-hidden="true">
      <div className={styles.row}>
        <Bone width={120} height={78} />
        <div className={styles.rowText}>
          <Bone width="46%" height={18} />
          <Bone width="62%" height={12} />
          <Bone width="38%" height={12} />
        </div>
      </div>
    </div>
  );
}

function LapSelectionCard() {
  return (
    <div className={styles.card} aria-hidden="true">
      <div className={styles.row}>
        <Bone width={150} height={30} />
        <Bone width={120} height={30} />
        <Bone width={100} height={30} />
      </div>
    </div>
  );
}

function TimelineCard({ rows = 7 }: { rows?: number }) {
  return (
    <div className={styles.card} aria-hidden="true">
      <Bone width={130} height={15} />
      <div className={styles.toolbar}>
        {Array.from({ length: 4 }, (_, index) => (
          <Bone key={index} width={86} height={28} round />
        ))}
      </div>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={styles.row}>
          <Bone width={14} height={14} round />
          <Bone width={56} height={11} />
          <Bone width={index % 2 ? "52%" : "64%"} height={12} />
        </div>
      ))}
    </div>
  );
}

function LapsHeader() {
  return (
    <div className={styles.head} aria-hidden="true">
      <div className={styles.headText}>
        <Bone width={190} height={22} />
        <Bone width={360} height={13} />
      </div>
      <div className={styles.toolbar}>
        <Bone width={230} height={44} style={{ borderRadius: 9 }} />
        <Bone width={230} height={44} style={{ borderRadius: 9 }} />
      </div>
    </div>
  );
}

/** Lap Analysis frame: header with pickers, view tabs, then main column beside the questions column. */
function LapsFrame({ children }: { children: ReactNode }) {
  return (
    <>
      <LapsHeader />
      <Toolbar tabs={6} extra={1} />
      <div className={styles.lapsLayout}>
        <div className={styles.stack}>{children}</div>
        <SkeletonCard rows={4} titleWidth={150} />
      </div>
    </>
  );
}

export function SkeletonBody({ variant }: { variant: SkeletonVariant }) {
  switch (variant) {
    case "drive":
      return (
        <>
          <SkeletonHead />
          <div className={styles.grid}>
            <SkeletonCard rows={4} titleWidth={150} />
            <SkeletonCard rows={4} titleWidth={120} />
            <SkeletonCard rows={3} titleWidth={170} />
            <SkeletonCard rows={3} titleWidth={130} />
          </div>
        </>
      );
    case "library":
      return (
        <>
          <SkeletonHead actions />
          <div className={styles.toolbar} aria-hidden="true">
            <Bone width={280} height={37} />
            <Bone width={140} height={37} />
            <Bone width={140} height={37} />
          </div>
          <div className={styles.card} aria-hidden="true">
            {Array.from({ length: 5 }, (_, index) => (
              <div key={index} className={styles.row}>
                <Bone width={72} height={48} />
                <div className={styles.rowText}>
                  <Bone width={index % 2 ? "42%" : "54%"} height={13} />
                  <Bone width={index % 2 ? "30%" : "38%"} height={10} />
                </div>
                <Bone width={72} height={27} />
              </div>
            ))}
          </div>
          <div className={styles.toolbar} aria-hidden="true" style={{ justifyContent: "center" }}>
            <Bone width={120} height={11} />
          </div>
        </>
      );
    case "overview":
      return (
        <>
          <Toolbar tabs={5} extra={1} />
          <HeroCard />
          <div className={styles.gridOverview}>
            <ChartCard height={240} title={170} />
            <SkeletonCard rows={3} titleWidth={140} />
          </div>
          <LapSelectionCard />
        </>
      );
    case "overview-table":
      return (
        <>
          <Toolbar tabs={5} extra={1} />
          <HeroCard />
          <TableCard rows={8} cols={6} title={120} />
          <LapSelectionCard />
        </>
      );
    case "overview-events":
      return (
        <>
          <Toolbar tabs={5} extra={1} />
          <HeroCard />
          <TimelineCard />
          <LapSelectionCard />
        </>
      );
    case "overview-setup":
      return (
        <>
          <Toolbar tabs={5} extra={1} />
          <HeroCard />
          <div className={styles.grid}>
            <SkeletonCard rows={4} titleWidth={150} />
            <SkeletonCard rows={4} titleWidth={130} />
          </div>
          <LapSelectionCard />
        </>
      );
    case "laps":
      return (
        <LapsFrame>
          <ChartCard height={190} title={180} />
          <div className={styles.gridWide}>
            <ChartCard height={170} title={150} />
            <SkeletonCard rows={3} titleWidth={130} />
          </div>
        </LapsFrame>
      );
    case "laps-charts":
      return (
        <LapsFrame>
          <ChartCard height={150} title={160} />
          <ChartCard height={110} title={140} />
          <ChartCard height={110} title={150} />
        </LapsFrame>
      );
    case "laps-table":
      return (
        <LapsFrame>
          <TableCard rows={6} cols={5} title={160} />
          <TableCard rows={4} cols={4} title={130} />
        </LapsFrame>
      );
    case "laps-map":
      return (
        <LapsFrame>
          <ChartCard height={300} title={140} />
          <TableCard rows={4} cols={4} title={120} />
        </LapsFrame>
      );
    case "settings":
      return (
        <>
          <SkeletonHead />
          <Toolbar tabs={5} />
          <div className={styles.grid}>
            <SkeletonCard rows={5} titleWidth={160} />
            <SkeletonCard rows={5} titleWidth={140} />
          </div>
          <div className={styles.tileRow} aria-hidden="true">
            {Array.from({ length: 5 }, (_, index) => (
              <div key={index} className={styles.card}>
                <Bone width="60%" height={11} />
                <Bone width="80%" height={14} />
              </div>
            ))}
          </div>
        </>
      );
  }
}
