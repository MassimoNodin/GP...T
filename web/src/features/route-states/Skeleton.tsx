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

export type SkeletonVariant = "drive" | "library" | "overview" | "laps" | "settings";

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
        </>
      );
    case "overview":
      return (
        <>
          <Toolbar tabs={4} />
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
          <div className={styles.gridWide}>
            <ChartCard height={240} title={170} />
            <SkeletonCard rows={3} titleWidth={140} />
          </div>
        </>
      );
    case "laps":
      return (
        <>
          <SkeletonHead actions />
          <Toolbar tabs={3} extra={2} />
          <ChartCard height={190} title={180} />
          <div className={styles.gridWide}>
            <ChartCard height={170} title={150} />
            <SkeletonCard rows={3} titleWidth={130} />
          </div>
        </>
      );
    case "settings":
      return (
        <>
          <SkeletonHead />
          <Toolbar tabs={4} />
          <SkeletonCard rows={5} titleWidth={160} />
        </>
      );
  }
}
