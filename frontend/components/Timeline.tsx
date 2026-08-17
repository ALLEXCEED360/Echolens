"use client";

import { useMemo, useState } from "react";
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
  const [hovered, setHovered] = useState<TimelineEvent | null>(null);

  const visible = useMemo(
    () => events.filter((e) => !hidden.has(e.type)),
    [events, hidden],
  );

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

  return (
    <div className="shrink-0">
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <h2 className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
          Timeline
        </h2>
        <div className="flex flex-wrap items-center gap-1.5">
          {Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([type, count]) => {
              const style = EVENT_STYLES[type] ?? {
                label: type,
                className: "bg-ink-400",
              };
              const off = hidden.has(type);
              return (
                <button
                  key={type}
                  onClick={() =>
                    setHidden((prev) => {
                      const next = new Set(prev);
                      next.has(type) ? next.delete(type) : next.add(type);
                      return next;
                    })
                  }
                  title={`${count} ${style.label.toLowerCase()} events`}
                  className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] transition-opacity ${
                    off ? "border-ink-800 opacity-40" : "border-ink-700"
                  }`}
                >
                  <span className={`h-1.5 w-1.5 rounded-full ${style.className}`} />
                  <span className="text-ink-400">{style.label}</span>
                  <span className="tabular text-ink-600">{count}</span>
                </button>
              );
            })}
        </div>
      </div>

      <div
        className="relative h-9 cursor-pointer overflow-hidden rounded border border-ink-800 bg-ink-900"
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          onSeek(((e.clientX - rect.left) / rect.width) * duration);
        }}
        onMouseLeave={() => setHovered(null)}
      >
        {buckets.map((event, i) =>
          event ? (
            <span
              key={i}
              onMouseEnter={() => setHovered(event)}
              style={{ left: `${(i / BUCKETS) * 100}%`, width: `${100 / BUCKETS}%` }}
              className={`absolute bottom-0 top-0 ${
                (EVENT_STYLES[event.type] ?? { className: "bg-ink-400" }).className
              } ${event.type === "topic_change" ? "opacity-100" : "opacity-50"}`}
            />
          ) : null,
        )}

        {/* Playhead */}
        <span
          style={{ left: `${playheadPct}%` }}
          className="pointer-events-none absolute bottom-0 top-0 w-px bg-ink-50"
        />
      </div>

      <div className="mt-1 flex h-4 items-center justify-between">
        <span className="tabular text-[10px] text-ink-600">0:00</span>
        {hovered ? (
          <span className="tabular truncate px-2 text-[10px] text-ink-300">
            {formatTimestamp(hovered.start_s)} · {hovered.title}
          </span>
        ) : (
          <span className="text-[10px] text-ink-600">click to seek</span>
        )}
        <span className="tabular text-[10px] text-ink-600">{formatTimestamp(duration)}</span>
      </div>
    </div>
  );
}
