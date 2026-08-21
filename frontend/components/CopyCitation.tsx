"use client";

import { Icon } from "@/components/ui/Icon";
import { formatCitation } from "@/lib/citation";
import type { CitationSource } from "@/lib/citation";
import { useCopy } from "@/lib/useCopy";

/**
 * Copy one moment as a quotable citation.
 *
 * The point of this whole system is that an answer can be traced back to the
 * second it came from. That guarantee stopped at the edge of the window: to
 * quote a line anywhere else you retyped it and hand-copied the timestamp,
 * which is exactly the step where a citation stops being accurate.
 *
 * Hidden until the row is hovered, so a list of forty of these is not forty
 * competing buttons — but only by opacity, never by `hidden`, so it stays in
 * the tab order and reappears on focus.
 */
export function CopyCitation({
  source,
  className = "",
  label = "Copy quote",
}: {
  source: CitationSource;
  className?: string;
  label?: string;
}) {
  const { state, copy } = useCopy();

  const tone =
    state === "copied"
      ? "text-accent-400"
      : state === "error"
        ? "text-warn-400"
        : "text-ink-500 hover:text-ink-200";

  return (
    <button
      type="button"
      // The row underneath is itself a seek control; without this the copy
      // click also jumps the playhead.
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        void copy(formatCitation(source));
      }}
      aria-label={label}
      title={state === "error" ? "Could not reach the clipboard" : label}
      className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded transition-all hover:bg-ink-800 focus-visible:opacity-100 focus-visible:outline-2 focus-visible:outline-accent-400 group-hover:opacity-100 ${
        state === "idle" ? "opacity-0" : "opacity-100"
      } ${tone} ${className}`}
    >
      <Icon
        name={state === "copied" ? "check" : state === "error" ? "alert" : "copy"}
        size={13}
      />
      <span className="sr-only" aria-live="polite">
        {state === "copied" ? "Citation copied" : state === "error" ? "Copy failed" : ""}
      </span>
    </button>
  );
}
