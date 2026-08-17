"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { SearchHit, SearchResponse } from "@/lib/types";

/**
 * Hybrid search with multimodal evidence.
 *
 * Every hit shows which retrievers found it, where reranking moved it, and what
 * else the pipeline recorded at that moment — the topic it sits in and the text
 * that was on screen. That is the difference between "the transcript mentions
 * colliders here" and "colliders were being explained while this code was up".
 *
 * The cross-encoder score is surfaced rather than hidden because it is
 * calibrated: below zero means nothing retrieved actually answers the query,
 * which is worth telling the user instead of presenting the least-bad match as
 * though it were an answer.
 */

/** Below this the reranker is saying nothing here is relevant. */
const RELEVANCE_FLOOR = 0;

interface Props {
  /** Restrict to one video. Omit to search the whole corpus. */
  videoId?: string;
  /** Restrict to one collection. Ignored when videoId is given. */
  collectionId?: string;
  /** Seek in-page instead of linking out — used on the video page. */
  onSeek?: (seconds: number) => void;
}

const KIND_FILTERS = [
  { value: "", label: "All" },
  { value: "transcript", label: "Spoken" },
  { value: "ocr", label: "On screen" },
];

export function SearchPanel({ videoId, collectionId, onSeek }: Props) {
  const [query, setQuery] = useState("");
  const [kinds, setKinds] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const run = useCallback(
    async (q: string, kindFilter: string) => {
      const trimmed = q.trim();
      if (trimmed.length < 2) return;

      // Guard against out-of-order responses: a slow earlier query must not
      // overwrite the results of a later one.
      const id = ++requestId.current;
      setLoading(true);
      setError(null);

      try {
        const response = await api.search(trimmed, {
          videoId,
          collectionId,
          limit: 15,
          kinds: kindFilter || undefined,
        });
        if (id === requestId.current) setResult(response);
      } catch (err) {
        if (id === requestId.current) {
          setError(err instanceof ApiError ? err.message : "Search failed.");
          setResult(null);
        }
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [videoId, collectionId],
  );

  const nothingRelevant =
    result !== null &&
    result.reranked &&
    result.top_relevance !== null &&
    result.top_relevance < RELEVANCE_FLOOR;

  return (
    <div className="flex min-h-0 flex-col">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run(query, kinds);
        }}
        className="flex gap-2"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={videoId ? "Search this video" : "Ask across all videos…"}
          className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-ink-200 placeholder:text-ink-600"
        />
        <button
          type="submit"
          disabled={loading || query.trim().length < 2}
          className="shrink-0 rounded border border-accent-500/40 px-3 py-2 text-xs text-accent-400 transition-colors hover:bg-accent-500/10 disabled:opacity-40"
        >
          {loading ? "…" : "Search"}
        </button>
      </form>

      <div className="mt-2 flex gap-1">
        {KIND_FILTERS.map((filter) => (
          <button
            key={filter.value}
            onClick={() => {
              setKinds(filter.value);
              if (result) void run(query, filter.value);
            }}
            className={`rounded border px-2 py-0.5 text-[10px] transition-colors ${
              kinds === filter.value
                ? "border-accent-500/40 text-accent-400"
                : "border-ink-700 text-ink-400 hover:text-ink-200"
            }`}
          >
            {filter.label}
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-2.5 rounded border border-danger-400/30 bg-danger-400/10 px-3 py-2 text-xs text-danger-400">
          {error}
        </p>
      )}

      {result && (
        <>
          <p className="tabular mt-3 shrink-0 text-[11px] text-ink-600">
            {result.total} result{result.total === 1 ? "" : "s"} · {result.took_ms.toFixed(0)}ms
            {result.reranked && result.rerank_ms != null
              ? ` (rerank ${result.rerank_ms.toFixed(0)}ms)`
              : ""}
            <span className="mx-1.5">·</span>
            {result.semantic_candidates} semantic, {result.lexical_candidates} lexical
          </p>

          {nothingRelevant && (
            <p className="mt-2 rounded border border-warn-400/30 bg-warn-400/5 px-3 py-2 text-[11px] text-warn-400">
              Nothing here looks like a real answer — the closest matches scored below the
              relevance floor. They are shown anyway, but treat them with suspicion.
            </p>
          )}

          {result.total === 0 && (
            <p className="mt-6 text-center text-xs text-ink-400">
              Nothing found for “{result.query}”.
            </p>
          )}

          <ul className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
            {result.hits.map((hit) => (
              <HitRow key={hit.chunk_id} hit={hit} showVideo={!videoId} onSeek={onSeek} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function HitRow({
  hit,
  showVideo,
  onSeek,
}: {
  hit: SearchHit;
  showVideo: boolean;
  onSeek?: (seconds: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const promoted = hit.fused_rank != null && hit.fused_rank > 1;

  const body = (
    <>
      <div className="flex items-baseline gap-2.5">
        <span className="tabular shrink-0 text-[11px] text-accent-400">
          {formatTimestamp(hit.start_s)}
        </span>
        {showVideo && (
          <span className="min-w-0 flex-1 truncate text-[11px] text-ink-400">
            {hit.video_title}
          </span>
        )}
        <RetrieverTags hit={hit} promoted={promoted} />
      </div>
      <p className="mt-1 whitespace-pre-line text-xs leading-5 text-ink-200">{hit.text}</p>
    </>
  );

  return (
    <li className="rounded-lg border border-ink-800 bg-ink-900 px-3 py-2.5 transition-colors hover:border-ink-600">
      {onSeek ? (
        <button onClick={() => onSeek(hit.start_s)} className="block w-full text-left">
          {body}
        </button>
      ) : (
        <Link href={`/videos/${hit.video_id}?t=${Math.floor(hit.start_s)}`} className="block">
          {body}
        </Link>
      )}

      {hit.context && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10px]">
          {hit.context.topic_title && (
            <span className="text-ink-500">
              <span className="text-ink-600">in </span>
              {hit.context.topic_title}
            </span>
          )}
          {hit.context.on_screen_text && (
            <span
              title={hit.context.on_screen_text}
              className="max-w-[220px] truncate text-warn-400/70"
            >
              screen: {hit.context.on_screen_text.split("\n").join(" ")}
            </span>
          )}
          {hit.context.events.length > 0 && (
            <span className="text-ink-600">{hit.context.events.length} events</span>
          )}
        </div>
      )}

      {hit.parent_text && (
        <>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="mt-1.5 text-[10px] uppercase tracking-wide text-ink-600 transition-colors hover:text-ink-400"
          >
            {expanded ? "hide context" : "show context"}
          </button>
          {expanded && (
            <p className="mt-1.5 border-l-2 border-ink-700 pl-2.5 text-[11px] leading-5 text-ink-400">
              {hit.parent_text}
            </p>
          )}
        </>
      )}
    </li>
  );
}

function RetrieverTags({ hit, promoted }: { hit: SearchHit; promoted: boolean }) {
  return (
    <span className="flex shrink-0 items-center gap-1">
      {promoted && (
        <span
          title={`Cross-encoder promoted this from rank ${hit.fused_rank}`}
          className="rounded bg-accent-500/15 px-1 py-0.5 text-[9px] text-accent-400"
        >
          ↑{hit.fused_rank}
        </span>
      )}
      {hit.rerank_score != null && (
        <span
          title="Cross-encoder relevance: above 2 is a genuine match"
          className={`tabular text-[9px] ${
            hit.rerank_score >= 2
              ? "text-accent-400"
              : hit.rerank_score >= 0
                ? "text-ink-400"
                : "text-danger-400"
          }`}
        >
          {hit.rerank_score.toFixed(1)}
        </span>
      )}
      {hit.matched_by.map((r) => (
        <span
          key={r}
          title={
            r === "semantic"
              ? `vector rank ${hit.semantic_rank}`
              : `keyword rank ${hit.lexical_rank}`
          }
          className={`rounded px-1 py-0.5 text-[9px] uppercase tracking-wide ${
            r === "semantic"
              ? "bg-accent-500/15 text-accent-400"
              : "bg-warn-400/15 text-warn-400"
          }`}
        >
          {r === "semantic" ? "vec" : "kw"}
        </span>
      ))}
    </span>
  );
}
