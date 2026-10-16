import { NotAvailable } from "@/features/ui-foundation/states";
import { Card, StatusBadge } from "@/features/ui-foundation/primitives";
import { TrackMap } from "@/features/ui-foundation/TrackMap";
import { TrackPhoto } from "@/features/ui-foundation/TrackPhoto";
import { UnitText } from "@/features/ui-foundation/units";
import type { HeroField, HeroPart, OverviewModel } from "./types";
import styles from "./SessionOverview.module.css";

function FieldValue({ field }: { field: HeroField }) {
  if (field.value === null) return <NotAvailable reason={field.reason ?? "Not reported"} />;
  return typeof field.value === "string" ? <UnitText>{field.value}</UnitText> : <>{field.value}</>;
}

function partText(part: HeroPart): string {
  return typeof part === "string" ? part : part.text;
}

export function Hero({
  hero,
  photo,
  chevron,
}: {
  hero: OverviewModel["hero"];
  photo: string | null;
  /** The status badge shows a chevron only where a menu exists (the fixture). */
  chevron: boolean;
}) {
  return (
    <Card className={styles.hero}>
      <div className={styles.heroTop}>
        <div className={styles.heroIdentity}>
          <TrackPhoto src={photo} alt={`${hero.title} circuit`} className={styles.heroPhoto} />
          <div className={styles.heroText}>
            <h1 className={styles.heroTitle}>{hero.title}</h1>
            {hero.lines.map((line) => (
              <div key={line.map(partText).join("-")} className={styles.heroLine}>
                {line.map((part, index) => (
                  <span key={`${partText(part)}-${index}`} title={typeof part === "string" ? undefined : part.title}>
                    {index > 0 && <span className={styles.dot}>•</span>}
                    <UnitText>{partText(part)}</UnitText>
                  </span>
                ))}
              </div>
            ))}
          </div>
          <TrackMap track={hero.track} className={styles.heroMap} strokeWidth={2.2} />
        </div>
        <div className={styles.heroStatsWrap}>
          <div className={styles.heroStatus}>
            <StatusBadge tone={hero.status.tone} chevron={chevron}>
              {hero.status.label}
            </StatusBadge>
          </div>
          <dl className={styles.heroStats}>
            {hero.stats.map((field) => (
              <div key={field.label}>
                <dd>
                  <FieldValue field={field} />
                </dd>
                <dt>{field.label}</dt>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <dl className={styles.heroDetails}>
        {hero.details.map((field) => (
          <div key={field.label}>
            <dt>{field.label}</dt>
            <dd>
              <FieldValue field={field} />
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
