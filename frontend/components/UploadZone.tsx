"use client";

import { Icon } from "@/components/ui/Icon";
import { ErrorNote } from "@/components/ui";

import { useCallback, useRef, useState } from "react";
import { ApiError, uploadVideo, type UploadHandle } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { VideoDetail } from "@/lib/types";

const ACCEPTED = [".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"];

interface Props {
  onUploaded: (video: VideoDetail) => void;
}

export function UploadZone({ onUploaded }: Props) {
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [current, setCurrent] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const handleRef = useRef<UploadHandle | null>(null);

  const upload = useCallback(
    async (file: File) => {
      const suffix = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!ACCEPTED.includes(suffix)) {
        setError(`${suffix || "That file"} is not a supported video format.`);
        return;
      }

      setError(null);
      setCurrent(file);
      setProgress(0);

      const handle = uploadVideo(file, { onProgress: setProgress });
      handleRef.current = handle;

      try {
        onUploaded(await handle.promise);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Upload failed.");
      } finally {
        setProgress(null);
        setCurrent(null);
        handleRef.current = null;
      }
    },
    [onUploaded],
  );

  const uploading = progress !== null;

  if (uploading) {
    // Server-side probing happens after the last byte lands, so the bar sits at
    // 100% for a moment. Say what is happening instead of looking stalled.
    const probing = progress >= 1;
    return (
      <div className="rounded-lg border border-ink-700 bg-ink-850 p-5">
        <div className="mb-2.5 flex items-baseline justify-between gap-4">
          <span className="truncate text-sm text-ink-200">{current?.name}</span>
          <span className="tabular shrink-0 text-xs text-ink-400">
            {probing ? "Probing…" : `${Math.round(progress * 100)}%`}
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-ink-800">
          <div
            className={`h-full rounded-full bg-accent-500 transition-[width] duration-200 ${probing ? "animate-pulse" : ""}`}
            style={{ width: `${Math.max(progress * 100, 2)}%` }}
          />
        </div>
        <div className="mt-2.5 flex items-center justify-between">
          <span className="tabular text-xs text-ink-400">
            {current ? formatBytes(current.size) : ""}
          </span>
          {!probing && (
            <button
              onClick={() => handleRef.current?.abort()}
              className="text-xs text-ink-400 transition-colors hover:text-danger-400"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) void upload(file);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        className={`cursor-pointer rounded-lg border border-dashed p-7 text-center transition-colors duration-150 ${
          dragging
            ? "border-accent-500 bg-accent-950"
            : "border-line-strong bg-surface hover:border-ink-600 hover:bg-raised"
        }`}
      >
        <span
          className={`mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full border transition-colors duration-150 ${
            dragging
              ? "border-accent-500 text-accent-400"
              : "border-line-strong text-ink-500"
          }`}
        >
          <Icon name="upload" size={18} />
        </span>
        <p className="text-sm text-ink-100">
          Drop a video here, or <span className="text-accent-400 underline underline-offset-2">browse</span>
        </p>
        <p className="mono mt-1.5 text-2xs text-ink-500">{ACCEPTED.join("  ·  ")}</p>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED.join(",")}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
          e.target.value = "";
        }}
      />

      {error && (
        <div className="mt-2.5">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
    </div>
  );
}
