import type { ReactNode, SVGProps } from "react";

export type IconProps = SVGProps<SVGSVGElement> & {
  size?: number;
  title?: string;
};

// Every UI icon is an inline SVG on a 24-unit grid with a shared stroke
// weight. Icons are decorative unless given a title.
function Svg({
  size = 16,
  title,
  children,
  strokeWidth = 1.7,
  ...rest
}: IconProps & { children: ReactNode }) {
  const labelled = typeof title === "string" && title.length > 0;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      role={labelled ? "img" : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-label={labelled ? title : undefined}
      {...rest}
    >
      {children}
    </svg>
  );
}

export function DriveIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 18v-5a8 8 0 0 1 16 0v5" />
      <path d="M4 18h3.5a1 1 0 0 0 1-1v-3.2a1 1 0 0 0-1-1H4" />
      <path d="M20 18h-3.5a1 1 0 0 1-1-1v-3.2a1 1 0 0 1 1-1H20" />
      <path d="M9 7.5h6" />
    </Svg>
  );
}

export function ReviewIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <path d="M8 9h8M8 12.5h5" />
      <path d="M8 16l2-1.5 2 1 2.5-2.5" />
    </Svg>
  );
}

export function SettingsIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </Svg>
  );
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <Svg strokeWidth={2} {...props}>
      <path d="M6 9l6 6 6-6" />
    </Svg>
  );
}

export function ChevronLeftIcon(props: IconProps) {
  return (
    <Svg strokeWidth={2} {...props}>
      <path d="M15 6l-6 6 6 6" />
    </Svg>
  );
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <Svg strokeWidth={2} {...props}>
      <path d="M9 6l6 6-6 6" />
    </Svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <Svg strokeWidth={2} {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M20 20l-4.2-4.2" />
    </Svg>
  );
}

export function SortIcon(props: IconProps) {
  return (
    <Svg strokeWidth={1.9} {...props}>
      <path d="M8 4v16M4.5 7.5L8 4l3.5 3.5" />
      <path d="M16 20V4M12.5 16.5L16 20l3.5-3.5" />
    </Svg>
  );
}

export function MoreIcon(props: IconProps) {
  return (
    <Svg {...props} stroke="none" fill="currentColor">
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </Svg>
  );
}

export function InfoIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.5" />
      <path d="M12 7.6v.1" strokeWidth={2.4} />
    </Svg>
  );
}

export function OpenDocumentIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6" />
      <path d="M15 3h6v6M21 3l-9 9" />
    </Svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Svg strokeWidth={2} {...props}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  );
}

export function ZoomIcon(props: IconProps) {
  return (
    <Svg strokeWidth={1.9} {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M20 20l-4.2-4.2M11 8.5v5M8.5 11h5" />
    </Svg>
  );
}

export function ThumbUpIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7 10v10H4.5A1.5 1.5 0 0 1 3 18.5v-7A1.5 1.5 0 0 1 4.5 10H7z" />
      <path d="M7 10l4-7a2.5 2.5 0 0 1 2.5 2.5V9h5.2a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.5 20H7" />
    </Svg>
  );
}

export function ThumbDownIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M17 14V4h2.5A1.5 1.5 0 0 1 21 5.5v7a1.5 1.5 0 0 1-1.5 1.5H17z" />
      <path d="M17 14l-4 7a2.5 2.5 0 0 1-2.5-2.5V15H5.3a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 6.5 4H17" />
    </Svg>
  );
}

export function MicrophoneIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="9" y="2.5" width="6" height="12" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21.5M8.5 21.5h7" />
    </Svg>
  );
}

export function SpeakerIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
      <path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />
    </Svg>
  );
}

export function GamepadIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7.5 7h9a4.5 4.5 0 0 1 4.4 3.6l.9 4.8a2.6 2.6 0 0 1-4.6 2.1L15.5 15h-7l-1.7 2.5a2.6 2.6 0 0 1-4.6-2.1l.9-4.8A4.5 4.5 0 0 1 7.5 7z" />
      <path d="M8 9.8v3M6.5 11.3h3" />
      <path d="M15.5 10.6v.1M17.5 12.2v.1" strokeWidth={2.4} />
    </Svg>
  );
}

export function StorageIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="5" y="3" width="14" height="18" rx="2.5" />
      <path d="M5 9h14M5 15h14" />
    </Svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Svg strokeWidth={2.4} {...props}>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </Svg>
  );
}

export function EngineerAvatarIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 15v-3a6 6 0 0 1 12 0v3" />
      <path d="M6 15h2v-3H6M18 15h-2v-3h2" />
      <path d="M16 15v1.5a2 2 0 0 1-2 2h-2" />
    </Svg>
  );
}

/** Small filled status dot with a glyph, as used throughout the reference. */
export function StatusGlyph({
  tone,
  glyph = "check",
  size = 14,
}: {
  tone: "green" | "amber" | "red" | "blue" | "grey" | "dark" | "purple";
  glyph?: "check" | "alert" | "dot" | "ring" | "info" | "user" | "square" | "flag";
  size?: number;
}) {
  const fill = {
    green: "var(--green)",
    amber: "var(--amber)",
    red: "var(--red)",
    blue: "var(--blue)",
    grey: "var(--line-strong)",
    dark: "var(--text-2)",
    purple: "var(--purple)",
  }[tone];
  if (glyph === "ring") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <circle cx="8" cy="8" r="5.6" fill="none" stroke={fill} strokeWidth="2.6" />
      </svg>
    );
  }
  if (glyph === "square") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <rect x="1.5" y="1.5" width="13" height="13" rx="3" fill={fill} />
        <path d="M5 8.2l2 2 4-4.2" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (glyph === "flag") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <rect x="1.5" y="1.5" width="13" height="13" rx="3" fill={fill} />
        <rect x="5" y="5" width="6" height="6" rx="1" fill="#fff" opacity="0.9" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="7.5" fill={fill} />
      {glyph === "check" && (
        <path d="M4.8 8.3l2.1 2.1 4.3-4.5" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      )}
      {glyph === "alert" && (
        <>
          <path d="M8 4.3v4.6" stroke="#fff" strokeWidth="1.9" strokeLinecap="round" />
          <circle cx="8" cy="11.4" r="1.05" fill="#fff" />
        </>
      )}
      {glyph === "info" && (
        <>
          <path d="M8 7.2v4.6" stroke="#fff" strokeWidth="1.9" strokeLinecap="round" />
          <circle cx="8" cy="4.6" r="1.05" fill="#fff" />
        </>
      )}
      {glyph === "dot" && <circle cx="8" cy="8" r="2.6" fill="#fff" />}
      {glyph === "user" && (
        <>
          <circle cx="8" cy="6.3" r="2.1" fill="#fff" />
          <path d="M4.4 12.2a3.8 3.8 0 0 1 7.2 0z" fill="#fff" />
        </>
      )}
    </svg>
  );
}

export type WaveformTone = "red" | "green" | "amber" | "blue" | "grey";

const WAVE_BARS = [3, 6, 4, 9, 12, 7, 10, 5, 3];

/**
 * Small audio waveform shown beside the current activity row. `mode` picks
 * the drawing: "static" bars (default, the original look), "animated" bars that
 * pulse only under no-preference motion (globals.css `.wave-bar`), or a "flat"
 * line. `dim` draws static bars at 40% opacity. Decorative.
 */
export function WaveformIcon({
  size = 36,
  mode = "static",
  tone = "red",
  dim,
}: {
  size?: number;
  mode?: "animated" | "static" | "flat";
  tone?: WaveformTone;
  dim?: boolean;
}) {
  const color = `var(--${tone === "grey" ? "faint" : tone})`;
  return (
    <svg
      width={size}
      height={size / 3}
      viewBox="0 0 36 12"
      aria-hidden="true"
      focusable="false"
      data-mode={mode}
    >
      {mode !== "flat" &&
        WAVE_BARS.map((h, i) => (
          <rect
            key={i}
            className={mode === "animated" ? "wave-bar" : undefined}
            style={mode === "animated" ? { animationDelay: `${(i % 5) * 90}ms` } : undefined}
            x={1 + i * 4}
            y={6 - h / 2}
            width="1.6"
            height={h}
            rx="0.8"
            fill={color}
            opacity={dim && mode === "static" ? 0.4 : undefined}
          />
        ))}
      <rect
        x="0"
        y="5.4"
        width="36"
        height="1.2"
        rx="0.6"
        fill={color}
        opacity={mode === "flat" ? 0.8 : 0.35}
      />
    </svg>
  );
}

export function AlertTriangleIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9.5v4.2" />
      <path d="M12 16.9v.1" strokeWidth={2.4} />
    </Svg>
  );
}

export function FileMissingIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <path d="M9.6 12.6l4.8 4.8M14.4 12.6l-4.8 4.8" />
    </Svg>
  );
}

export function SwapIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7.5 4L3.5 8l4 4M3.5 8H18" />
      <path d="M16.5 20l4-4-4-4M20.5 16H6" />
    </Svg>
  );
}

export function UploadIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 15.5V4M7.5 8.5L12 4l4.5 4.5" />
      <path d="M4.5 15v3.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V15" />
    </Svg>
  );
}

export function DownloadIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 4v11.5M7.5 11L12 15.5 16.5 11" />
      <path d="M4.5 15v3.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V15" />
    </Svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Svg strokeWidth={2} {...props}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Svg>
  );
}

export function RefreshIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
      <path d="M20.5 4v5h-5" />
    </Svg>
  );
}

export function PlayIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 5.5v13l10.5-6.5z" />
    </Svg>
  );
}

export function PauseIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="6.5" y="5" width="3.8" height="14" rx="1" />
      <rect x="13.7" y="5" width="3.8" height="14" rx="1" />
    </Svg>
  );
}

export function StopIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </Svg>
  );
}

export function CopyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" />
    </Svg>
  );
}

/** Clock with a counter-clockwise arrow. */
export function HistoryIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 12A8.5 8.5 0 1 0 6 6" />
      <path d="M3.5 4v4.5H8" />
      <path d="M12 7.5V12l3 2" />
    </Svg>
  );
}

export function SendIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M21 3L10.5 13.5" />
      <path d="M21 3l-6.5 18-4-7.5L3 9.5z" />
    </Svg>
  );
}

/** Wrench, used for pit stops. */
export function PitIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </Svg>
  );
}

/** Chequered flag outline. */
export function FlagIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 21V3.5" />
      <path d="M5 4h14v10H5M12 4v10M5 9h14" />
      <path d="M5 4h7v5H5zM12 9h7v5h-7z" fill="currentColor" stroke="none" opacity="0.85" />
    </Svg>
  );
}

/** Car seen from above, for overtakes and collisions. */
export function CarIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="7.5" y="3" width="9" height="18" rx="3.5" />
      <path d="M9.3 8h5.4M9.3 16h5.4" />
      <path d="M5.2 7v3M18.8 7v3M5.2 15v3M18.8 15v3" strokeWidth={2.2} />
    </Svg>
  );
}

export type WeatherKind =
  | "clear"
  | "light_cloud"
  | "overcast"
  | "light_rain"
  | "heavy_rain"
  | "storm";

const CLOUD = "M7.5 17.5a4 4 0 0 1-.4-7.98 5.5 5.5 0 0 1 10.6 1.3A3.4 3.4 0 0 1 17 17.5z";
const SUN_RAYS =
  "M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4";

export function WeatherIcon({ kind, ...props }: IconProps & { kind: WeatherKind }) {
  return (
    <Svg {...props}>
      {kind === "clear" && (
        <>
          <circle cx="12" cy="12" r="4" />
          <path d={SUN_RAYS} />
        </>
      )}
      {kind === "light_cloud" && (
        <>
          <circle cx="8" cy="8" r="2.8" />
          <path d="M8 2.5v1.2M2.5 8h1.2M4.1 4.1l.9.9M11.9 4.1L11 5" />
          <path d={CLOUD} transform="translate(2 2.5)" />
        </>
      )}
      {kind === "overcast" && <path d={CLOUD} transform="translate(0 1.5)" />}
      {kind === "light_rain" && (
        <>
          <path d={CLOUD} transform="translate(0 -1.5)" />
          <path d="M9 18.5l-.8 2.2M14.5 18.5l-.8 2.2" />
        </>
      )}
      {kind === "heavy_rain" && (
        <>
          <path d={CLOUD} transform="translate(0 -1.5)" />
          <path d="M8 18l-1 2.8M12 18l-1 2.8M16 18l-1 2.8" />
        </>
      )}
      {kind === "storm" && (
        <>
          <path d={CLOUD} transform="translate(0 -1.5)" />
          <path d="M12.8 14.5l-2.6 3.6h3.4l-2.2 3.4" />
        </>
      )}
    </Svg>
  );
}

export type TyreCompound =
  | "soft"
  | "medium"
  | "hard"
  | "inter"
  | "wet"
  | "super_soft"
  | "dry"
  | "unknown";

const TYRE_COLOR: Record<TyreCompound, string> = {
  soft: "var(--stint-soft)",
  super_soft: "var(--stint-soft)",
  medium: "var(--stint-medium)",
  hard: "var(--stint-hard)",
  dry: "var(--stint-hard)",
  inter: "var(--stint-inter)",
  wet: "var(--stint-wet)",
  unknown: "var(--grey-soft)",
};

/**
 * Tyre seen face-on: outer ring in the compound colour, white inner ring. The
 * compound letter is never drawn; the label is always visible text beside it.
 */
export function TyreIcon({ compound, ...props }: IconProps & { compound: TyreCompound }) {
  const color = TYRE_COLOR[compound] ?? TYRE_COLOR.unknown;
  return (
    <Svg {...props} strokeWidth={1}>
      <circle cx="12" cy="12" r="10" fill={color} stroke="var(--border-strong)" />
      <circle cx="12" cy="12" r="7" fill="none" stroke="var(--surface)" strokeWidth={1.4} />
      <circle cx="12" cy="12" r="4.2" fill="var(--surface)" stroke="none" />
    </Svg>
  );
}

/** Document with a waveform, for capture files. */
export function CaptureFileIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <path d="M8.5 15v2M11 12.5v6M13.5 14v3" />
    </Svg>
  );
}

export function ExpandIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
    </Svg>
  );
}
