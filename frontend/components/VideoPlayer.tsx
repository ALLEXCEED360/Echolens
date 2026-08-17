"use client";

import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { api } from "@/lib/api";
import { formatTimestamp, parseTimestamp } from "@/lib/format";

/**
 * Imperative handle for timestamp navigation.
 *
 * This is the interface every later phase depends on: clicking a transcript
 * line, an event marker or an answer citation all resolve to `seek(seconds)`.
 * Wiring it now means Phase 2 only has to supply the timestamps.
 */
export interface VideoPlayerHandle {
  seek: (seconds: number) => void;
  play: () => void;
  pause: () => void;
  currentTime: () => number;
}

interface Props {
  videoId: string;
  playable: boolean;
  onTimeUpdate?: (seconds: number) => void;
}

export const VideoPlayer = forwardRef<VideoPlayerHandle, Props>(function VideoPlayer(
  { videoId, playable, onTimeUpdate },
  ref,
) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);

  useImperativeHandle(ref, () => ({
    seek: (seconds: number) => {
      const el = videoRef.current;
      if (!el) return;
      // Clamping avoids the browser silently ignoring an out-of-bounds seek,
      // which looks identical to a broken citation.
      el.currentTime = Math.max(0, Math.min(seconds, el.duration || seconds));
    },
    // `play()` returns a promise that rejects with AbortError whenever a seek
    // or pause lands before playback starts — routine when clicking through a
    // timeline, but an unhandled rejection in the console if left uncaught.
    play: () => {
      videoRef.current?.play().catch(() => undefined);
    },
    pause: () => videoRef.current?.pause(),
    currentTime: () => videoRef.current?.currentTime ?? 0,
  }));

  if (!playable) {
    return (
      <div className="flex aspect-video items-center justify-center rounded-lg border border-ink-700 bg-ink-900 p-8 text-center">
        <div>
          <p className="text-sm text-ink-200">This container needs transcoding to play here.</p>
          <p className="mt-1.5 text-xs text-ink-400">
            It is stored and will be indexed normally — only in-browser playback is affected.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="shrink-0 overflow-hidden rounded-lg border border-ink-800 bg-black">
      <video
        ref={videoRef}
        src={api.streamUrl(videoId)}
        controls
        preload="metadata"
        // Capped so the transcript keeps usable height: for a 6-hour video the
        // transcript is the primary surface, not the picture.
        className="mx-auto max-h-[45vh] w-full bg-black object-contain"
        onTimeUpdate={(e) => onTimeUpdate?.(e.currentTarget.currentTime)}
        onError={() =>
          setError("Playback failed — the codec may be unsupported by this browser.")
        }
      />
      {error && (
        <p className="border-t border-ink-800 bg-ink-900 px-3 py-2 text-xs text-danger-400">
          {error}
        </p>
      )}
    </div>
  );
});

/** Seek control.
 *
 * Phase 1 has no transcript to click, so this exercises the same `seek()` path
 * the citation UI will use — and verifies range requests are working, since a
 * seek into an unbuffered region is exactly a 206 request.
 */
export function SeekBar({
  onSeek,
  currentTime,
  duration,
}: {
  onSeek: (seconds: number) => void;
  currentTime: number;
  duration: number | null;
}) {
  const [value, setValue] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const seconds = parseTimestamp(value);
    if (seconds !== null) onSeek(seconds);
  };

  return (
    <form onSubmit={submit} className="flex items-center gap-2.5">
      <span className="tabular shrink-0 text-xs text-ink-400">
        {formatTimestamp(currentTime)} / {formatTimestamp(duration)}
      </span>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Jump to 12:30"
        aria-label="Jump to timestamp"
        className="w-32 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-xs text-ink-200 placeholder:text-ink-600"
      />
      <button
        type="submit"
        className="rounded border border-ink-700 px-2.5 py-1 text-xs text-ink-300 transition-colors hover:border-accent-500 hover:text-accent-400"
      >
        Go
      </button>
    </form>
  );
}
