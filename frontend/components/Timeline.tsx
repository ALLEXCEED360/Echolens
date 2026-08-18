"use client";

import { useMemo, useRef, useState } from "react";
import { PanelHeader } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";
import { EVENT_STYLES, type TimelineEvent } from "@/lib/types";

/**
 * Event markers laid out proportionally across the video's duration.
 *
 * A 6-hour video produces ~580 events, so at typical widths several land on the
 * same pixel. Rather than overplot, markers are bucketed per pixel column and
 * the bucket takes the colour of its highest-priority event — topic changes are
 * the structural signal and must never be hidden behind a scene cut.
 */

interface Props {
  events: TimelineEvent[];
  duration: number;
  currentTime: number;
  onSeek: (seconds: number) => void;
}

// Lower index wins when several events share a column.
const PRIORITY = ["topic_change", "text_appeared", "silence", "scene_change", "slide_change"];

const BUCKETS = 240;

export function Timeline({ events, duration, currentTime, onSeek }: Props) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [hovered, setHovered] = useState<{ event: TimelineEvent; pct: number } | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);

  const visible = useMemo(() => events.filter((e) => !hidden.has(e.type)), [events, hidden]);

  const buckets = useMemo(() => {
    if (!duration) return [];
    const slots: (TimelineEvent | null)[] = Array.from({ length: BUCKETS }, () => null);

    for (const event of visible) {
      const index = Math.min(
        BUCKETS - 1,
        Math.max(0, Math.floor((event.start_s / duration) * BUCKETS)),
      );
      const held = slots[index];
      if (!held) {
        slots[index] = event;
        continue;
      }
      const rank = (e: TimelineEvent) => {
        const i = PRIORITY.indexOf(e.type);
        return i === -1 ? PRIORITY.length : i;
      };
      if (rank(event) < rank(held)) slots[index] = event;
    }
    return slots;
  }, [visible, duration]);

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const e of events) out[e.type] = (out[e.type] ?? 0) + 1;
    return out;
  }, [events]);

  if (events.length === 0 || !duration) return null;

  const playheadPct = Math.min((currentTime / duration) * 100, 100);

  const seekFromPointer = (clientX: number) => {
    const rect = trackRef.current?.getBoundingClientRect();
    if (!rect) return;
    onSeek(((clientX - rect.left) / rect.width) * duration);
  };

  return (
    <>
      <PanelHeader title="Timeline" icon="timeline" count={`${events.length} events`}>
        {/* Legend doubles as a filter. Each entry states what it is, how many
            there are, and whether it is currently shown. */}
        <div className="flex flex-wrap items-center justify-end gap-1">
          {Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([type, count]) => {
              const style = EVENT_STYLES[type] ?? { label: type, className: "bg-ink-400" };
              const off = hidden.has(type);
              return (
                <button
                  key={type}
                  type="button"
                  aria-pressed={!off}
                  onClick={() =>
                    setHidden((prev) => {
                      const next = new Set(prev);
                      if (next.has(type)) next.delete(type);
                      else next.add(type);
                      return next;
                    })
                  }
                  title={`${count} ${style.label.toLowerCase()} events — click to ${
                    off ? "show" : "hide"
                  }`}
                  className={`inline-flex h-7 cursor-pointer items-center gap-1.5 rounded border px-1.5 text-2xs transition-colors duration-150 ${
                    off
                      ? "border-line text-ink-500"
                      : "border-line-strong text-ink-300 hover:text-ink-50"
                  }`}
                >
                  <span
                    className={`h-2 w-2 rounded-full ${style.className} ${off ? "opacity-25" : ""}`}
                  />
                  {style.label}
                  <span className="tabular text-ink-500">{count}</span>
                </button>
              );
            })}
        </div>
      </PanelHeader>

      <div className="px-4 pb-3.5 pt-3">
        <div
          ref={trackRef}
          role="slider"
          tabIndex={0}
          aria-label="Video timeline"
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(currentTime)}
          aria-valuetext={formatTimestamp(currentTime)}
          className="relative h-12 cursor-pointer overflow-hidden rounded-md border border-line bg-ink-950"
          onClick={(e) => seekFromPointer(e.clientX)}
          onKeyDown={(e) => {
            const step = e.shiftKey ? 60 : 10;
            if (e.key === "ArrowRight") {
              e.preventDefault();
              onSeek(Math.min(currentTime + step, duration));
            } else if (e.key === "ArrowLeft") {
              e.preventDefault();
              onSeek(Math.max(currentTime - step, 0));
            }
          }}
          onMouseLeave={() => setHovered(null)}
        >
          {/* Minute/hour gridlines give the bar a sense of scale — without them
              a position reads as "somewhere in the middle" and nothing more. */}
          {Array.from({ length: 11 }, (_, i) => (
            <span
              key={i}
              style={{ left: `${(i + 1) * (100 / 12)}%` }}
              className="absolute inset-y-0 w-px bg-line/70"
              aria-hidden
            />
          ))}

          {buckets.map((event, i) =>
            event ? (
              <span
                key={i}
                onMouseEnter={() => setHovered({ event, pct: (i / BUCKETS) * 100 })}
                style={{ left: `${(i / BUCKETS) * 100}%`, width: `${100 / BUCKETS}%` }}
                className={`absolute ${
                  event.type === "topic_change"
                    ? "inset-y-0 opacity-100"
                    : "bottom-0 top-1/3 opacity-60"
                } ${(EVENT_STYLES[event.type] ?? { className: "bg-ink-400" }).className}`}
              />
            ) : null,
          )}

          {/* Playhead. A hairline is invisible against a busy bar, so it gets a
              contrasting outline and a handle. */}
          <span
            style={{ left: `${playheadPct}%` }}
            className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-ink-50 shadow-[0_0_0_1px_rgba(0,0,0,0.7)]"
          >
            <span className="absolute -top-px left-1/2 h-1.5 w-1.5 -translate-x-1/2 rotate-45 bg-ink-50" />
          </span>

          {hovered && (
            <span
              style={{ left: `${hovered.pct}%` }}
              className="pointer-events-none absolute inset-y-0 w-px -translate-x-1/2 bg-ink-50/50"
            />
          )}
        </div>

        <div className="mt-1.5 flex h-4 items-center justify-between gap-3">
          <span className="tabular shrink-0 text-2xs text-ink-500">0:00</span>
          {hovered ? (
            <span className="tabular min-w-0 truncate text-2xs text-ink-200">
              <span
                className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${
                  (EVENT_STYLES[hovered.event.type] ?? { className: "bg-ink-400" }).className
                }`}
              />
              {formatTimestamp(hovered.event.start_s)} · {hovered.event.title}
            </span>
          ) : (
            <span className="text-2xs text-ink-500">
              Click to seek · arrow keys to nudge
            </span>
          )}
          <span className="tabular shrink-0 text-2xs text-ink-500">
            {formatTimestamp(duration)}
          </span>
        </div>
      </div>
    </>
  );
}
