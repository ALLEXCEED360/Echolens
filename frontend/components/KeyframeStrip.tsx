"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { Badge, PanelHeader } from "@/components/ui";
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
  framesWithText?: number;
}

export function KeyframeStrip({ keyframes, currentTime, onSeek, framesWithText }: Props) {
  const [textOnly, setTextOnly] = useState(false);
  const [hovered, setHovered] = useState<Keyframe | null>(null);
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

  const withText =
    framesWithText ?? keyframes.filter((k) => k.text.trim().length > 0).length;

  return (
    <>
      <PanelHeader
        title="Keyframes"
        icon="image"
        count={`${keyframes.length.toLocaleString()} frames`}
      >
        {withText > 0 ? (
          <button
            type="button"
            aria-pressed={textOnly}
            onClick={() => setTextOnly((v) => !v)}
            className={`inline-flex h-7 cursor-pointer items-center gap-1.5 rounded border px-2 text-2xs font-medium transition-colors duration-150 ${
              textOnly
                ? "border-accent-600/60 bg-accent-950 text-accent-300"
                : "border-line-strong text-ink-400 hover:text-ink-100"
            }`}
          >
            <Icon name="type" size={11} strokeWidth={2} />
            With text
            <span className="tabular opacity-70">{withText.toLocaleString()}</span>
          </button>
        ) : (
          // Not a neutral zero: it means OCR has not run against these frames.
          <Badge tone="warn" icon="alert" title="Run the OCR stage to read on-screen text">
            no text read
          </Badge>
        )}
      </PanelHeader>

      <div className="px-3 pb-3 pt-2.5">
        <div
          className="flex gap-1.5 overflow-x-auto pb-2"
          onMouseLeave={() => setHovered(null)}
        >
          {visible.map((frame, i) => {
            const active = i === activeIndex;
            return (
              <button
                key={frame.id}
                ref={active ? activeRef : undefined}
                onClick={() => onSeek(frame.start_s)}
                onMouseEnter={() => setHovered(frame)}
                onFocus={() => setHovered(frame)}
                aria-label={`Jump to ${formatTimestamp(frame.start_s)}`}
                className={`group relative shrink-0 cursor-pointer overflow-hidden rounded-md border transition-all duration-150 ${
                  active
                    ? "border-accent-400 ring-1 ring-accent-400/40"
                    : "border-line hover:border-ink-500"
                }`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={api.keyframeImageUrl(frame.image_url)}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  width={128}
                  height={72}
                  className={`h-[72px] w-[128px] bg-ink-900 object-cover transition-opacity duration-150 ${
                    active ? "opacity-100" : "opacity-75 group-hover:opacity-100"
                  }`}
                />
                <span className="tabular absolute bottom-0 left-0 rounded-tr bg-canvas/85 px-1 py-px text-2xs text-ink-100">
                  {formatTimestamp(frame.start_s)}
                </span>
                {frame.text && (
                  <span
                    title="On-screen text was read from this frame"
                    className="absolute right-1 top-1 rounded bg-accent-500 p-0.5 text-ink-950"
                  >
                    <Icon name="type" size={9} strokeWidth={2.5} />
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Reserve the row whether or not something is hovered, so the panel
            below does not jump as the pointer crosses the strip. */}
        <div className="mt-1 h-9 overflow-hidden">
          {hovered?.text ? (
            <p className="mono line-clamp-2 rounded border border-line bg-ink-900 px-2.5 py-1.5 text-xs leading-4 text-ink-400">
              {hovered.text.split("\n").join("  ")}
            </p>
          ) : (
            <p className="px-0.5 py-1.5 text-2xs text-ink-500">
              Hover a frame to read the text on screen. Click to jump there.
            </p>
          )}
        </div>
      </div>
    </>
  );
}
