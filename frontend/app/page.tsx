"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { UploadZone } from "@/components/UploadZone";
import { ApiError, api } from "@/lib/api";
import { formatBytes, formatRelativeDate, formatResolution, formatTimestamp } from "@/lib/format";
import type { VideoSummary } from "@/lib/types";

export default function LibraryPage() {
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (q: string) => {
    try {
      const data = await api.listVideos({ q: q || undefined, limit: 100 });
      setVideos(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the library.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Debounced so typing in the filter does not issue a request per keystroke.
    const timer = setTimeout(() => void load(query), query ? 250 : 0);
    return () => clearTimeout(timer);
  }, [query, load]);

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight text-ink-50">Library</h1>
        <p className="mt-1 text-sm text-ink-400">
          Upload a video to inspect its metadata and play it back.
        </p>
      </div>

      <UploadZone onUploaded={() => void load(query)} />

      <div className="mt-8">
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-sm font-medium text-ink-200">
            {videos.length > 0 ? `${videos.length} video${videos.length === 1 ? "" : "s"}` : ""}
          </h2>
          {(videos.length > 0 || query) && (
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by title"
              className="w-56 rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-200 placeholder:text-ink-600"
            />
          )}
        </div>

        {error && (
          <p className="rounded border border-danger-400/30 bg-danger-400/10 px-3 py-2.5 text-xs text-danger-400">
            {error}
          </p>
        )}

        {!error && loading && <p className="py-8 text-center text-xs text-ink-400">Loading…</p>}

        {!error && !loading && videos.length === 0 && (
          <p className="py-10 text-center text-xs text-ink-400">
            {query ? `Nothing matching “${query}”.` : "No videos yet."}
          </p>
        )}

        <ul className="space-y-1.5">
          {videos.map((video) => (
            <li key={video.id}>
              <Link
                href={`/videos/${video.id}`}
                className="flex items-center gap-4 rounded-lg border border-ink-800 bg-ink-900 px-4 py-3 transition-colors hover:border-ink-600 hover:bg-ink-850"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-ink-50">{video.title}</p>
                  <p className="tabular mt-0.5 text-xs text-ink-400">
                    {formatTimestamp(video.duration_s)}
                    <span className="mx-1.5 text-ink-700">·</span>
                    {formatResolution(video.width, video.height)}
                    <span className="mx-1.5 text-ink-700">·</span>
                    {formatBytes(video.size_bytes)}
                    {!video.has_audio && (
                      <>
                        <span className="mx-1.5 text-ink-700">·</span>
                        <span className="text-warn-400">no audio</span>
                      </>
                    )}
                  </p>
                </div>
                <StatusPill status={video.status} />
                <span className="shrink-0 text-xs text-ink-600">
                  {formatRelativeDate(video.created_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const styles: Record<string, string> = {
    uploaded: "border-ink-600 text-ink-300",
    processing: "border-warn-400/40 text-warn-400",
    ready: "border-accent-500/40 text-accent-400",
    failed: "border-danger-400/40 text-danger-400",
    uploading: "border-ink-700 text-ink-400",
  };
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${styles[status] ?? styles.uploaded}`}
    >
      {status}
    </span>
  );
}
