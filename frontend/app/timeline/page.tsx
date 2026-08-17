"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { CollectionSummary, ConceptTimeline, VideoTrack } from "@/lib/types";

/**
 * Concept timeline — where an idea appears across the corpus.
 *
 * A ranked list answers "what are the best matches". This answers "when is it
 * introduced, where is it developed, and does it come back" — the question you
 * actually ask of a course. Occurrences are chronological within each video and
 * videos are ordered by how well they cover the concept.
 */
export default function TimelinePage() {
  const [query, setQuery] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [result, setResult] = useState<ConceptTimeline | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listCollections().then((l) => setCollections(l.items)).catch(() => undefined);
  }, []);

  const run = useCallback(async () => {
    const trimmed = query.trim();
    if (trimmed.length < 2) return;
    setLoading(true);
    setError(null);
    try {
      setResult(
        await api.conceptTimeline(trimmed, {
          collectionId: collectionId || undefined,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not build a timeline.");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [query, collectionId]);

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <div className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight text-ink-50">Concept timeline</h1>
        <p className="mt-1 text-sm text-ink-400">
          Trace where an idea appears across your videos — introduced, developed, revisited.
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
        className="flex gap-2"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. colliders, prefabs, coroutines"
          className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-ink-200 placeholder:text-ink-600"
        />
        {collections.length > 0 && (
          <select
            value={collectionId}
            onChange={(e) => setCollectionId(e.target.value)}
            className="shrink-0 rounded border border-ink-700 bg-ink-900 px-2 py-2 text-xs text-ink-300"
          >
            <option value="">All videos</option>
            {collections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
        <button
          type="submit"
          disabled={loading || query.trim().length < 2}
          className="shrink-0 rounded border border-accent-500/40 px-3 py-2 text-xs text-accent-400 hover:bg-accent-500/10 disabled:opacity-40"
        >
          {loading ? "…" : "Trace"}
        </button>
      </form>

      {error && (
        <p className="mt-3 rounded border border-danger-400/30 bg-danger-400/10 px-3 py-2 text-xs text-danger-400">
          {error}
        </p>
      )}

      {result && !loading && (
        <div className="mt-5">
          {result.total_occurrences === 0 ? (
            <p className="text-xs text-ink-400">
              No confident mentions of “{result.query}”. Either it is not covered, or it is
              discussed in words too different for retrieval to connect.
            </p>
          ) : (
            <>
              <p className="tabular mb-4 text-[11px] text-ink-500">
                {result.total_occurrences} mention
                {result.total_occurrences === 1 ? "" : "s"} across {result.tracks.length} video
                {result.tracks.length === 1 ? "" : "s"} · {result.took_ms.toFixed(0)}ms
                {result.first_video_title && result.first_start_s != null && (
                  <>
                    <span className="mx-1.5">·</span>
                    earliest in {result.first_video_title} at{" "}
                    <Link
                      href={`/videos/${result.first_video_id}?t=${Math.floor(result.first_start_s)}`}
                      className="text-accent-400"
                    >
                      {formatTimestamp(result.first_start_s)}
                    </Link>
                  </>
                )}
              </p>

              <div className="space-y-5">
                {result.tracks.map((track) => (
                  <Track key={track.video_id} track={track} />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Track({ track }: { track: VideoTrack }) {
  const duration = track.duration_s ?? 0;

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <Link
          href={`/videos/${track.video_id}`}
          className="truncate text-xs font-medium text-ink-100 hover:text-accent-400"
        >
          {track.video_title}
        </Link>
        <span className="tabular shrink-0 text-[10px] text-ink-600">
          {track.occurrences.length} mention{track.occurrences.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* Positional strip: where in the video the mentions cluster. */}
      {duration > 0 && (
        <div className="relative mb-2 h-4 overflow-hidden rounded border border-ink-800 bg-ink-900">
          {track.occurrences.map((o) => (
            <Link
              key={o.chunk_id}
              href={`/videos/${track.video_id}?t=${Math.floor(o.start_s)}`}
              title={`${formatTimestamp(o.start_s)} — ${o.text.slice(0, 120)}`}
              style={{ left: `${Math.min((o.start_s / duration) * 100, 99.5)}%` }}
              className="absolute bottom-0 top-0 w-1 bg-accent-400/70 hover:bg-accent-400"
            />
          ))}
        </div>
      )}

      <ul className="space-y-1">
        {track.occurrences.map((o) => (
          <li key={o.chunk_id}>
            <Link
              href={`/videos/${track.video_id}?t=${Math.floor(o.start_s)}`}
              className="flex gap-2.5 rounded px-1.5 py-1 transition-colors hover:bg-ink-850"
            >
              <span className="tabular shrink-0 text-[10px] leading-5 text-accent-400">
                {formatTimestamp(o.start_s)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="line-clamp-1 text-[11px] leading-5 text-ink-300">
                  {o.text}
                </span>
                {o.topic_title && (
                  <span className="text-[10px] text-ink-600">in {o.topic_title}</span>
                )}
              </span>
              {o.relevance != null && (
                <span className="tabular shrink-0 text-[10px] leading-5 text-ink-600">
                  {o.relevance.toFixed(1)}
                </span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
