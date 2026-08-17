"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { Keyframe } from "@/lib/types";

/**
 * Filmstrip of extracted keyframes, aligned to the playhead.
 *
 * Each thumbnail is one visually stable span, not a fixed-interval sample, so
 * the strip is a map of where the picture *changed* — which for a screencast or
 * a slide deck is the structure you actually want to navigate by.
 */

interface Props {
  keyframes: Keyframe[];
  currentTime: number;
  onSeek: (seconds: number) => void;
}

export function KeyframeStrip({ keyframes, currentTime, onSeek }: Props) {
  const [textOnly, setTextOnly] = useState(false);
  const [hovered, setHovered] = useState<Keyframe | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);

  const visible = useMemo(
    () => (textOnly ? keyframes.filter((k) => k.text.trim().length > 0) : keyframes),
    [keyframes, textOnly],
  );

  // Binary search for the frame covering the playhead — the strip can hold
  // thousands and this runs on every tick.
  const activeIndex = useMemo(() => {
    let lo = 0;
    let hi = visible.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (visible[mid].start_s <= currentTime) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }, [visible, currentTime]);

  const activeId = activeIndex >= 0 ? visible[activeIndex].id : null;

  useEffect(() => {
    if (!activeId) return;
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }, [activeId]);

  if (keyframes.length === 0) return null;

  const withText = keyframes.filter((k) => k.text.trim().length > 0).length;

  return (
    <div className="shrink-0">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
          Keyframes
        </h2>
        <div className="flex items-center gap-2.5">
          <span className="tabular text-[10px] text-ink-600">
            {keyframes.length.toLocaleString()} frames · {withText.toLocaleString()} with text
          </span>
          <button
            onClick={() => setTextOnly((v) => !v)}
            disabled={withText === 0}
            className={`rounded border px-2 py-0.5 text-[10px] transition-colors disabled:opacity-40 ${
              textOnly
                ? "border-accent-500/40 text-accent-400"
                : "border-ink-700 text-ink-400 hover:text-ink-200"
            }`}
          >
            With text
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="flex gap-1.5 overflow-x-auto pb-2"
        onMouseLeave={() => setHovered(null)}
      >
        {visible.map((frame, i) => (
          <button
            key={frame.id}
            ref={i === activeIndex ? activeRef : undefined}
            onClick={() => onSeek(frame.start_s)}
            onMouseEnter={() => setHovered(frame)}
            title={`${formatTimestamp(frame.start_s)}${frame.text ? ` — ${frame.text.slice(0, 120)}` : ""}`}
            className={`relative shrink-0 overflow-hidden rounded border transition-colors ${
              i === activeIndex
                ? "border-accent-500"
                : "border-ink-800 hover:border-ink-600"
            }`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.keyframeImageUrl(frame.image_url)}
              alt=""
              loading="lazy"
              decoding="async"
              className="h-[72px] w-[128px] bg-ink-900 object-cover"
            />
            <span className="tabular absolute bottom-0 left-0 bg-ink-950/80 px-1 text-[9px] text-ink-200">
              {formatTimestamp(frame.start_s)}
            </span>
            {frame.text && (
              <span
                title="text detected"
                className="absolute right-0 top-0 bg-accent-500/80 px-1 text-[9px] text-ink-950"
              >
                T
              </span>
            )}
          </button>
        ))}
      </div>

      {hovered?.text && (
        <p className="mt-1 max-h-16 overflow-y-auto whitespace-pre-line rounded border border-ink-800 bg-ink-900 px-2.5 py-1.5 text-[11px] leading-4 text-ink-400">
          {hovered.text}
        </p>
      )}
    </div>
  );
}
