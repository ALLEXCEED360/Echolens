/**
 * Inline SVG icons.
 *
 * A dependency-free set rather than `lucide-react`: this UI needs about
 * fifteen glyphs, and shipping an icon package to get them costs more than it
 * saves. Paths follow Lucide's 24×24 / 2px-stroke geometry so they sit
 * together consistently.
 *
 * **Icons are decorative by default** — `aria-hidden`, with meaning carried by
 * adjacent text. An icon that is the only label needs a real one on the
 * control itself; see `IconButton`.
 */

export type IconName =
  | "search"
  | "sparkles"
  | "layers"
  | "timeline"
  | "video"
  | "play"
  | "pause"
  | "upload"
  | "download"
  | "trash"
  | "refresh"
  | "chevron-right"
  | "chevron-down"
  | "external"
  | "close"
  | "check"
  | "alert"
  | "info"
  | "clock"
  | "type"
  | "image"
  | "quote"
  | "copy"
  | "arrow-right"
  | "filter"
  | "pencil"
  | "plus";

const PATHS: Record<IconName, React.ReactNode> = {
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  sparkles: (
    <>
      <path d="M12 3.5 13.6 8 18 9.6 13.6 11.2 12 15.7 10.4 11.2 6 9.6 10.4 8Z" />
      <path d="M18 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8Z" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 13 9 5 9-5" />
    </>
  ),
  timeline: (
    <>
      <path d="M4 6h16M4 12h10M4 18h13" />
      <circle cx="18" cy="12" r="1.6" />
    </>
  ),
  video: (
    <>
      <rect x="3" y="6" width="12" height="12" rx="2" />
      <path d="m15 10.5 5-3v9l-5-3Z" />
    </>
  ),
  play: <path d="M7 5.5v13l11-6.5-11-6.5Z" />,
  pause: (
    <>
      <rect x="7" y="5" width="3.5" height="14" rx="1" />
      <rect x="13.5" y="5" width="3.5" height="14" rx="1" />
    </>
  ),
  upload: (
    <>
      <path d="M12 16V4" />
      <path d="m7.5 8.5 4.5-4.5 4.5 4.5" />
      <path d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v12" />
      <path d="m7.5 11.5 4.5 4.5 4.5-4.5" />
      <path d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16" />
      <path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7" />
      <path d="M6.5 7 7 19a1.5 1.5 0 0 0 1.5 1.4h7A1.5 1.5 0 0 0 17 19l.5-12" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.4-5.7" />
      <path d="M20 4v4.5h-4.5" />
    </>
  ),
  "chevron-right": <path d="m9.5 5.5 6.5 6.5-6.5 6.5" />,
  "chevron-down": <path d="m5.5 9.5 6.5 6.5 6.5-6.5" />,
  external: (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M18 14v5a1.5 1.5 0 0 1-1.5 1.5H5.5A1.5 1.5 0 0 1 4 19V7.5A1.5 1.5 0 0 1 5.5 6H10" />
    </>
  ),
  close: <path d="M6 6 18 18M18 6 6 18" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  alert: (
    <>
      <path d="M12 4.5 21 19.5H3L12 4.5Z" />
      <path d="M12 10v4" />
      <circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5" />
      <circle cx="12" cy="8" r="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  type: (
    <>
      <path d="M5 7V5.5h14V7" />
      <path d="M12 5.5V19" />
      <path d="M9 19h6" />
    </>
  ),
  image: (
    <>
      <rect x="3.5" y="5" width="17" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.6" />
      <path d="m4.5 17 4.5-4 4 3.5 3-2.5 4 3.5" />
    </>
  ),
  quote: (
    <>
      <path d="M9 6H5.5A1.5 1.5 0 0 0 4 7.5V11a1.5 1.5 0 0 0 1.5 1.5H8V15a3 3 0 0 1-3 3" />
      <path d="M20 6h-3.5A1.5 1.5 0 0 0 15 7.5V11a1.5 1.5 0 0 0 1.5 1.5H19V15a3 3 0 0 1-3 3" />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5.5 15H5a1.5 1.5 0 0 1-1.5-1.5V5A1.5 1.5 0 0 1 5 3.5h8.5A1.5 1.5 0 0 1 15 5v.5" />
    </>
  ),
  "arrow-right": (
    <>
      <path d="M4 12h15" />
      <path d="m13.5 6.5 6 5.5-6 5.5" />
    </>
  ),
  filter: <path d="M4 6h16l-6.5 7.5V19l-3 1.5v-7L4 6Z" />,
  pencil: (
    <>
      <path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3Z" />
      <path d="m14.5 6.5 3 3" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
};

export function Icon({
  name,
  size = 16,
  className = "",
  strokeWidth = 1.75,
}: {
  name: IconName;
  size?: number;
  className?: string;
  strokeWidth?: number;
}) {
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
      className={`shrink-0 ${className}`}
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

/** The wordmark. Concentric arcs — a lens, and a sound reflecting off it. */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9.25" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.35" />
      <circle cx="12" cy="12" r="5.75" fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.65" />
      <circle cx="12" cy="12" r="2.25" fill="currentColor" />
    </svg>
  );
}
