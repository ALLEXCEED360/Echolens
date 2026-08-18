"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import type { TranscriptSegment } from "@/lib/types";

/**
 * Virtualised transcript list.
 *
 * A 6-hour tutorial produces ~6,600 segments. Rendering them all cost 568 ms of
 * blocked main thread whenever the list was rebuilt — on first load and every
 * time the search box was cleared. That is a visible freeze.
 *
 * Virtualisation renders only the ~40 rows in view, which makes list
 * construction constant-time regardless of video length. It also gives random
 * access via `scrollToIndex`, which is what jumping to a cited timestamp needs
 * (Phase 7) — infinite scroll could not do that.
 *
 * With only ~40 rows mounted, re-rendering on every playhead tick is cheap, so
 * the active-row highlight stays plain declarative React.
 */

interface Props {
  segments: TranscriptSegment[];
  currentTime: number;
  onSeek: (seconds: number) => void;
}

/** Index of the segment covering `t`. Assumes ascending, non-overlapping starts. */
function findActive(segments: TranscriptSegment[], t: number): number {
  let lo = 0;
  let hi = segments.length - 1;
  let found = -1;

  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].start_s <= t) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }

  // Past the end of the last started segment nothing is active. The 0.5s
  // tolerance spans inter-segment gaps so the highlight does not flicker.
  if (found < 0) return -1;
  return t <= segments[found].end_s + 0.5 ? found : -1;
}

export function TranscriptPanel({ segments, currentTime, onSeek }: Props) {
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  const needle = filter.trim().toLowerCase();

  const visible = useMemo(
    () => (needle ? segments.filter((s) => s.text.toLowerCase().includes(needle)) : segments),
    [segments, needle],
  );

  const virtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => scrollRef.current,
    // Most segments are one line; measurement corrects the ones that wrap.
    estimateSize: () => 32,
    overscan: 12,
    getItemKey: useCallback((i: number) => visible[i]?.id ?? i, [visible]),
    // The library re-renders via flushSync by default so measurements apply
    // before paint. Called from the measure ref — which React runs during
    // commit — that produces a stream of "flushSync was called from inside a
    // lifecycle method" errors. Normal scheduling is fast enough here; rows
    // are 1-3 lines, so a mispredicted height is at most a few pixels.
    useFlushSync: false,
  });

  const activeIndex = findActive(visible, currentTime);
  const activeId = activeIndex >= 0 ? visible[activeIndex].id : null;
  const lastIndexRef = useRef(-1);

  // Keyed on the segment id, so this fires once per segment change rather than
  // on every playhead tick within the same segment.
  useEffect(() => {
    if (!follow || activeIndex < 0) return;

    const previous = lastIndexRef.current;
    lastIndexRef.current = activeIndex;

    // Smooth scrolling is right for ordinary playback, where the list advances
    // a row at a time. It is wrong for a jump: seeking to 4:00:00 in a 6-hour
    // transcript would animate across ~190,000px, taking seconds and rendering
    // every window in between. Jumps snap instantly.
    const isJump = previous < 0 || Math.abs(activeIndex - previous) > 40;

    // Deferred out of the effect: scrollToIndex calls flushSync internally, and
    // React refuses to flush while it is already rendering.
    const frame = requestAnimationFrame(() => {
      virtualizer.scrollToIndex(activeIndex, {
        align: "center",
        behavior: isJump ? "auto" : "smooth",
      });
    });
    return () => cancelAnimationFrame(frame);
    // virtualizer identity is stable; activeIndex is derived from activeId
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, follow]);

  if (segments.length === 0) return null;

  const items = virtualizer.getVirtualItems();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <Input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          icon="search"
          aria-label="Filter transcript"
          placeholder={`Filter ${segments.length.toLocaleString()} segments`}
          className="min-w-0 flex-1"
        />
        {filter && (
          <button
            type="button"
            onClick={() => setFilter("")}
            aria-label="Clear filter"
            className="shrink-0 cursor-pointer rounded p-1.5 text-ink-500 transition-colors hover:text-ink-100"
          >
            <Icon name="close" size={14} />
          </button>
        )}
        <button
          type="button"
          onClick={() => setFollow((f) => !f)}
          aria-pressed={follow}
          title="Keep the transcript scrolled to the playhead"
          className={`inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors duration-150 ${
            follow
              ? "border-accent-600/60 bg-accent-950 text-accent-300"
              : "border-line-strong text-ink-400 hover:text-ink-100"
          }`}
        >
          <Icon name="arrow-right" size={12} strokeWidth={2} />
          Follow
        </button>
      </div>

      {needle && (
        <p className="border-b border-line px-3.5 py-1.5 text-2xs text-ink-500">
          {visible.length.toLocaleString()} of {segments.length.toLocaleString()} segments match
          <span className="mono text-ink-400"> “{filter}”</span>
        </p>
      )}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
        {/* Spacer sized to the full list so the scrollbar reflects reality. */}
        <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {items.map((item) => {
            const segment = visible[item.index];
            const active = item.index === activeIndex;
            return (
              <div
                key={item.key}
                ref={virtualizer.measureElement}
                data-index={item.index}
                className="transcript-row absolute left-0 top-0 w-full"
                style={{ transform: `translateY(${item.start}px)` }}
              >
                <button
                  onClick={() => onSeek(segment.start_s)}
                  className={`relative flex w-full cursor-pointer gap-3 rounded-md py-1.5 pl-3 pr-2 text-left transition-colors duration-150 ${
                    active ? "bg-accent-950" : "hover:bg-ink-850"
                  }`}
                >
                  {/* The spoken line you are on gets a rail, not just a tint —
                      a background wash alone is easy to lose while scrolling. */}
                  {active && (
                    <span
                      className="absolute inset-y-1 left-0 w-0.5 rounded-full bg-accent-400"
                      aria-hidden
                    />
                  )}
                  <span
                    className={`tabular row-time shrink-0 text-2xs leading-5 ${
                      active ? "text-accent-400" : "text-ink-500"
                    }`}
                  >
                    {formatTimestamp(segment.start_s)}
                  </span>
                  <span
                    className={`row-text text-sm leading-5 ${
                      active ? "text-ink-50" : "text-ink-300"
                    }`}
                  >
                    {needle ? highlight(segment.text, needle) : segment.text}
                  </span>
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function highlight(text: string, needle: string) {
  // Escape regex metacharacters — users type things like "C++" and "∂L/∂w".
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));

  return parts.map((part, i) =>
    part.toLowerCase() === needle ? (
      <mark key={i} className="rounded-sm bg-accent-500/25 px-0.5 text-accent-300">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}
