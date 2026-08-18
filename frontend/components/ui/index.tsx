/**
 * UI primitives.
 *
 * The old UI had no shared vocabulary: every button was a one-off `className`,
 * so a destructive Delete looked exactly like a routine Re-run, and thirteen
 * different text sizes appeared across six pages. These are the building
 * blocks — if a screen needs something outside them, that is a signal the set
 * is missing a piece, not a licence to hand-roll another variant.
 *
 * Rules baked in here rather than left to each caller:
 *   - every interactive element has a hover state and a visible focus ring
 *   - hit targets are at least 32px tall, and 44px for primary touch controls
 *   - destructive actions look destructive
 *   - icon-only controls carry a real accessible label
 */

"use client";

import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

/* ── Button ─────────────────────────────────────────────────────────────── */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md";

const BUTTON_BASE =
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium " +
  "transition-colors duration-150 cursor-pointer select-none " +
  "disabled:pointer-events-none disabled:opacity-40";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent-500 text-ink-950 hover:bg-accent-400 active:bg-accent-600 font-semibold",
  secondary:
    "bg-ink-750 text-ink-100 hover:bg-ink-700 border border-line-strong",
  ghost: "text-ink-300 hover:text-ink-50 hover:bg-ink-800",
  // Destructive actions must not be one indistinguishable button among
  // several. Colour alone is not enough, so these also carry an icon.
  danger:
    "bg-transparent text-danger-400 border border-danger-400/35 hover:bg-danger-950 hover:border-danger-400/70",
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-2.5 text-xs",
  md: "h-9 px-3.5 text-sm",
};

export function Button({
  children,
  variant = "secondary",
  size = "md",
  icon,
  iconRight,
  className = "",
  ...props
}: {
  children?: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IconName;
  iconRight?: IconName;
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
      {...props}
    >
      {icon && <Icon name={icon} size={size === "sm" ? 13 : 15} />}
      {children}
      {iconRight && <Icon name={iconRight} size={size === "sm" ? 13 : 15} />}
    </button>
  );
}

/** An icon-only control. `label` is required — it becomes the accessible name. */
export function IconButton({
  name,
  label,
  size = "md",
  variant = "ghost",
  className = "",
  ...props
}: {
  name: IconName;
  label: string;
  size?: ButtonSize;
  variant?: ButtonVariant;
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      aria-label={label}
      title={label}
      className={`${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${
        size === "sm" ? "h-8 w-8" : "h-9 w-9"
      } ${className}`}
      {...props}
    >
      <Icon name={name} size={size === "sm" ? 14 : 16} />
    </button>
  );
}

/* ── Surfaces ───────────────────────────────────────────────────────────── */

export function Panel({
  children,
  className = "",
  inset = false,
}: {
  children: ReactNode;
  className?: string;
  inset?: boolean;
}) {
  return (
    <section
      className={`rounded-lg border border-line bg-surface ${inset ? "p-4" : ""} ${className}`}
    >
      {children}
    </section>
  );
}

/**
 * A labelled section head.
 *
 * Previously these were 10px uppercase grey text, visually identical to the
 * content beneath — so nothing announced where one region ended and the next
 * began. A heading should be findable without reading it.
 */
export function PanelHeader({
  title,
  icon,
  count,
  children,
}: {
  title: string;
  icon?: IconName;
  count?: ReactNode;
  children?: ReactNode;
}) {
  return (
    // `min-h` rather than a fixed height, and the trailing controls are allowed
    // to wrap onto their own row: at 375px the timeline legend is five chips
    // wide and collided with the title when the header could not grow.
    <header className="flex min-h-11 flex-wrap items-center gap-x-2 gap-y-1 border-b border-line px-4 py-2">
      {icon && <Icon name={icon} size={15} className="text-ink-500" />}
      <h2 className="text-sm font-semibold tracking-tight text-ink-100">{title}</h2>
      {count != null && <span className="tabular text-2xs text-ink-500">{count}</span>}
      {children && (
        <div className="flex w-full items-center gap-1.5 sm:ml-auto sm:w-auto sm:justify-end">
          {children}
        </div>
      )}
    </header>
  );
}

/* ── Badge ──────────────────────────────────────────────────────────────── */

type BadgeTone = "neutral" | "accent" | "warn" | "danger" | "info";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "border-line-strong text-ink-400",
  accent: "border-accent-600/50 bg-accent-950 text-accent-300",
  warn: "border-warn-400/35 bg-warn-950 text-warn-300",
  danger: "border-danger-400/35 bg-danger-950 text-danger-300",
  info: "border-info-400/35 text-info-400",
};

export function Badge({
  children,
  tone = "neutral",
  icon,
  title,
  className = "",
}: {
  children: ReactNode;
  tone?: BadgeTone;
  icon?: IconName;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-px text-2xs font-medium whitespace-nowrap ${BADGE_TONES[tone]} ${className}`}
    >
      {icon && <Icon name={icon} size={11} strokeWidth={2} />}
      {children}
    </span>
  );
}

/**
 * Status with a shape as well as a colour, so it survives colour-blindness
 * and greyscale printing.
 */
export function StatusDot({ status }: { status: string }) {
  const map: Record<string, { tone: BadgeTone; icon: IconName; label: string }> = {
    ready: { tone: "accent", icon: "check", label: "Indexed" },
    processing: { tone: "info", icon: "refresh", label: "Processing" },
    queued: { tone: "neutral", icon: "clock", label: "Queued" },
    failed: { tone: "danger", icon: "alert", label: "Failed" },
    uploaded: { tone: "neutral", icon: "upload", label: "Not indexed" },
  };
  const entry = map[status] ?? { tone: "neutral" as BadgeTone, icon: "info" as IconName, label: status };
  return (
    <Badge tone={entry.tone} icon={entry.icon}>
      {entry.label}
    </Badge>
  );
}

/* ── Form controls ──────────────────────────────────────────────────────── */

const FIELD_BASE =
  "w-full rounded-md border border-line-strong bg-ink-900 text-ink-100 " +
  "placeholder:text-ink-500 transition-colors duration-150 " +
  "hover:border-ink-600 focus:border-accent-600 focus:outline-none " +
  "focus-visible:outline-2 focus-visible:outline-accent-400 focus-visible:outline-offset-1";

export function Input({
  className = "",
  icon,
  ...props
}: { className?: string; icon?: IconName } & React.InputHTMLAttributes<HTMLInputElement>) {
  if (!icon) {
    return <input className={`${FIELD_BASE} h-9 px-3 text-sm ${className}`} {...props} />;
  }
  return (
    <div className={`relative ${className}`}>
      <Icon
        name={icon}
        size={15}
        className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-500"
      />
      <input className={`${FIELD_BASE} h-9 pl-8 pr-3 text-sm`} {...props} />
    </div>
  );
}

export function Select({
  className = "",
  children,
  ...props
}: { className?: string } & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`${FIELD_BASE} h-9 cursor-pointer px-2.5 text-sm ${className}`} {...props}>
      {children}
    </select>
  );
}

/** A segmented control. Clearer than a `<select>` for two-to-four options. */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string; icon?: IconName }[];
  label: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex rounded-md border border-line-strong bg-ink-900 p-0.5"
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={`inline-flex h-7 cursor-pointer items-center gap-1.5 rounded px-2.5 text-xs font-medium transition-colors duration-150 ${
              active
                ? "bg-ink-700 text-ink-50"
                : "text-ink-400 hover:text-ink-100"
            }`}
          >
            {option.icon && <Icon name={option.icon} size={12} strokeWidth={2} />}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/* ── Feedback ───────────────────────────────────────────────────────────── */

export function Spinner({ size = 14, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={`animate-spin ${className}`}
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2.5" opacity="0.2" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** A loading placeholder that reserves the final height, so nothing shifts. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse-soft rounded bg-ink-800 ${className}`} aria-hidden />;
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon: IconName;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <Icon name={icon} size={22} className="text-ink-500" />
      <p className="text-sm font-medium text-ink-300">{title}</p>
      {hint && <p className="max-w-sm text-xs leading-5 text-ink-500">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="flex items-start gap-2 rounded-md border border-danger-400/30 bg-danger-950 px-3 py-2 text-xs leading-5 text-danger-300"
    >
      <Icon name="alert" size={14} className="mt-0.5" />
      <span>{children}</span>
    </p>
  );
}

/* ── Page furniture ─────────────────────────────────────────────────────── */

export function PageHeader({
  title,
  subtitle,
  back,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  back?: { href: string; label: string };
  children?: ReactNode;
}) {
  return (
    <div className="mb-6">
      {back && (
        <Link
          href={back.href}
          className="mb-2 inline-flex items-center gap-1 text-xs text-ink-400 transition-colors hover:text-accent-400"
        >
          <Icon name="chevron-right" size={13} className="rotate-180" />
          {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight text-ink-50">{title}</h1>
          {subtitle && (
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-400">
              {subtitle}
            </div>
          )}
        </div>
        {children && <div className="flex shrink-0 items-center gap-2">{children}</div>}
      </div>
    </div>
  );
}

/** A metadata separator. A real element, not a "·" glued into a string. */
export function Dot() {
  return <span className="text-ink-500">·</span>;
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="tabular text-md font-semibold text-ink-50">{value}</div>
      <div className="text-2xs uppercase tracking-wide text-ink-500">{label}</div>
    </div>
  );
}

/* ── Tabs ───────────────────────────────────────────────────────────────── */

/**
 * A tabbed rail.
 *
 * The video page previously stacked seven panels in a 320px column, so the
 * flagship Ask box got about eighty pixels and everything below it was
 * off-screen. Tabs are progressive disclosure: one region at a time, each with
 * the full height of the rail.
 *
 * Roving focus follows the WAI-ARIA tabs pattern — arrow keys move between
 * tabs, so a keyboard user is not forced to tab through every panel.
 */
export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  tabs: { value: T; label: string; icon?: IconName; badge?: ReactNode }[];
  label: string;
}) {
  const move = (delta: number) => {
    const index = tabs.findIndex((t) => t.value === value);
    const next = tabs[(index + delta + tabs.length) % tabs.length];
    if (next) onChange(next.value);
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className="flex shrink-0 items-center gap-0.5 border-b border-line px-2"
      onKeyDown={(event) => {
        if (event.key === "ArrowRight") {
          event.preventDefault();
          move(1);
        } else if (event.key === "ArrowLeft") {
          event.preventDefault();
          move(-1);
        }
      }}
    >
      {tabs.map((tab) => {
        const active = tab.value === value;
        return (
          <button
            key={tab.value}
            role="tab"
            type="button"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(tab.value)}
            className={`relative inline-flex h-10 cursor-pointer items-center gap-1.5 px-2.5 text-xs font-medium transition-colors duration-150 ${
              active ? "text-ink-50" : "text-ink-400 hover:text-ink-100"
            }`}
          >
            {tab.icon && <Icon name={tab.icon} size={14} />}
            {tab.label}
            {tab.badge != null && (
              <span className="tabular text-2xs text-ink-500">{tab.badge}</span>
            )}
            {active && (
              <span className="absolute inset-x-1.5 -bottom-px h-0.5 rounded-full bg-accent-400" />
            )}
          </button>
        );
      })}
    </div>
  );
}

/** A disclosure. Collapsed by default so reference data does not crowd tools. */
export function Disclosure({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="group border-b border-line last:border-b-0" open={defaultOpen}>
      <summary className="flex h-10 cursor-pointer list-none items-center gap-1.5 px-4 text-xs font-medium text-ink-300 transition-colors hover:text-ink-50 [&::-webkit-details-marker]:hidden">
        <Icon
          name="chevron-right"
          size={13}
          className="text-ink-500 transition-transform duration-150 group-open:rotate-90"
        />
        {title}
      </summary>
      <div className="px-4 pb-3.5">{children}</div>
    </details>
  );
}

/** A label/value row for reference data. */
export function DataRow({
  label,
  value,
  title,
  mono = false,
}: {
  label: string;
  value: ReactNode;
  title?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <dt className="shrink-0 text-xs text-ink-500">{label}</dt>
      <dd
        className={`truncate text-xs text-ink-200 ${mono ? "mono" : "tabular"}`}
        title={title}
      >
        {value}
      </dd>
    </div>
  );
}

/* ── Confirmation ───────────────────────────────────────────────────────── */

/**
 * A confirmation dialog.
 *
 * Replaces `window.confirm`, which is not merely ugly — it is unreliable. After
 * a page shows a couple of dialogs Chrome offers "Prevent this page from
 * creating additional dialogs", and once that is ticked `confirm()` silently
 * returns `false` for the rest of the page's life. The button then does nothing
 * at all, with no dialog, no error and no way to tell that the browser ate it.
 * That is exactly how Delete "stopped working".
 *
 * It is also blocking, unstyleable, and cannot say what is about to be lost.
 *
 * Implemented on `<dialog>` so the browser supplies the modal semantics: focus
 * is trapped, Escape closes, and the rest of the page is inert to assistive
 * technology.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Delete",
  destructive = true,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onCancel={(event) => {
        // Escape fires `cancel`; let React own the state rather than the DOM.
        event.preventDefault();
        if (!busy) onCancel();
      }}
      onClick={(event) => {
        // Backdrop clicks land on the dialog itself, not its contents.
        if (event.target === ref.current && !busy) onCancel();
      }}
      aria-labelledby="confirm-title"
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-lg border border-line-strong bg-raised p-0 text-ink-200 shadow-pop backdrop:bg-black/60 backdrop:backdrop-blur-[1px]"
    >
      <div className="px-5 pb-4 pt-5">
        <div className="flex items-start gap-3">
          <span
            className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
              destructive ? "bg-danger-950 text-danger-400" : "bg-ink-800 text-ink-300"
            }`}
          >
            <Icon name={destructive ? "trash" : "info"} size={16} />
          </span>
          <div className="min-w-0">
            <h2 id="confirm-title" className="text-md font-semibold text-ink-50">
              {title}
            </h2>
            {body && <div className="mt-1.5 text-sm leading-6 text-ink-300">{body}</div>}
          </div>
        </div>
      </div>
      <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant={destructive ? "danger" : "primary"}
          icon={destructive ? "trash" : undefined}
          onClick={onConfirm}
          disabled={busy}
          autoFocus
        >
          {busy ? "Deleting…" : confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
