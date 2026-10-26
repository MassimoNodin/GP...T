import styles from "./TrackPhoto.module.css";

/**
 * Circuit photograph slot. Drop a photo at web/public/tracks/<track>.jpg (or
 * .png/.webp) and the server-side resolver passes its URL here; until then a
 * neutral placeholder keeps the reference layout.
 */
export function TrackPhoto({
  src,
  alt,
  className,
}: {
  src: string | null;
  alt: string;
  className?: string;
}) {
  const classes = [styles.photo, className].filter(Boolean).join(" ");
  if (src) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img className={classes} src={src} alt={alt} loading="lazy" decoding="async" />
    );
  }
  return <span className={`${classes} ${styles.placeholder}`} role="img" aria-label={alt} />;
}
