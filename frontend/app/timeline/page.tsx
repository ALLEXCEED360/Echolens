"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button, EmptyState, ErrorNote, Input, PageHeader, Select, Skeleton } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type {
  CollectionSummary,
  ConceptTimeline,
  VideoSummary,
  VideoTrack,
} from "@/lib/types";

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
  /**
   * Scope is one control over two kinds of thing, so the value carries its own
   * type: "collection:<id>" or "video:<id>". Previously this held a bare
   * collection id under a label reading "All videos" — which listed
   * collections, offered no way to scope to a single video even though the API
   * accepts one, and left the actual videos missing from a menu named after
   * them.
   */
  const [scope, setScope] = useState("");
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [result, setResult] = useState<ConceptTimeline | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listCollections().then((l) => setCollections(l.items)).catch(() => undefined);
    api.listVideos().then((l) => setVideos(l.items)).catch(() => undefined);
  }, []);

  const run = useCallback(async () => {
    const trimmed = query.trim();
    if (trimmed.length < 2) return;
    setLoading(true);
    setError(null);
    try {
      const [kind, id] = scope.split(":");
      setResult(
        await api.conceptTimeline(trimmed, {
          collectionId: kind === "collection" ? id : undefined,
          videoId: kind === "video" ? id : undefined,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not build a timeline.");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, [query, scope]);

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <PageHeader
        title="Concept timeline"
        subtitle="Trace where an idea appears — introduced, developed, revisited. Ordered by time, not by rank."
      />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
        className="flex flex-wrap gap-2"
      >
        <Input
          value={query}
          icon="timeline"
          aria-label="Concept to trace"
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. colliders, prefabs, coroutines"
          className="min-w-0 flex-1"
        />
        {(collections.length > 0 || videos.length > 0) && (
          <Select
            aria-label="Limit the trace to a collection or a single video"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            className="w-full shrink-0 sm:max-w-[200px]"
          >
            <option value="">Everything</option>
            {collections.length > 0 && (
              <optgroup label="Collections">
                {collections.map((c) => (
                  <option key={c.id} value={`collection:${c.id}`}>
                    {c.name}
                  </option>
                ))}
              </optgroup>
            )}
            {videos.length > 0 && (
              <optgroup label="Videos">
                {videos.map((v) => (
                  <option key={v.id} value={`video:${v.id}`}>
                    {v.title}
                  </option>
                ))}
              </optgroup>
            )}
          </Select>
        )}
        <Button
          type="submit"
          variant="primary"
          disabled={loading || query.trim().length < 2}
          className="shrink-0"
        >
          {loading ? "Tracing…" : "Trace"}
        </Button>
      </form>

      {error && (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {loading && (
        <div className="mt-5 space-y-2" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      )}

      {result && !loading && (
        <div className="mt-5">
          {result.total_occurrences === 0 ? (
            <EmptyState
              icon="timeline"
              title={`No confident mentions of “${result.query}”`}
              hint="Either the corpus does not cover it, or it is discussed in words too different for retrieval to connect. Try the wording someone would actually say."
            />
          ) : (
            <>
              <p className="tabular mb-4 text-xs text-ink-500">
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
        <span className="tabular shrink-0 text-2xs text-ink-500">
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
              <span className="tabular shrink-0 text-2xs leading-5 text-accent-400">
                {formatTimestamp(o.start_s)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="line-clamp-1 text-xs leading-5 text-ink-300">
                  {o.text}
                </span>
                {o.topic_title && (
                  <span className="text-2xs text-ink-500">in {o.topic_title}</span>
                )}
              </span>
              {o.relevance != null && (
                <span className="tabular shrink-0 text-2xs leading-5 text-ink-500">
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
