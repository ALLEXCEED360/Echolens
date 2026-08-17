"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { AnswerResponse, Citation } from "@/lib/types";

/**
 * Ask a question, get an answer whose every claim is clickable.
 *
 * The answer text arrives with inline `[c_N]` markers. Each is rendered as a
 * timestamp resolved server-side from the database — the model never wrote it,
 * so it cannot be wrong. Markers the model invented were rejected before this
 * component ever saw them, and `fabricated_citations` reports how many.
 */

const CITATION_RE = /\[c_(\d+)\]/g;

interface Props {
  /** Restrict to one video. Omit to ask across the whole corpus. */
  videoId?: string;
  /** Restrict to one collection. Ignored when videoId is given. */
  collectionId?: string;
  /** Seek in-page instead of linking out — used on the video page. */
  onSeek?: (seconds: number) => void;
}

export function AskPanel({ videoId, collectionId, onSeek }: Props) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AnswerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  const requestId = useRef(0);

  const ask = useCallback(
    async (q: string) => {
      const trimmed = q.trim();
      if (trimmed.length < 3) return;

      const id = ++requestId.current;
      setLoading(true);
      setError(null);
      setShowEvidence(false);

      try {
        const response = await api.ask(trimmed, { videoId, collectionId });
        if (id === requestId.current) setResult(response);
      } catch (err) {
        if (id === requestId.current) {
          setError(err instanceof ApiError ? err.message : "Could not answer that.");
          setResult(null);
        }
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [videoId, collectionId],
  );

  return (
    <div className="flex min-h-0 flex-col">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
        className="flex gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={videoId ? "Ask about this video…" : "Ask about your videos…"}
          className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-ink-200 placeholder:text-ink-600"
        />
        <button
          type="submit"
          disabled={loading || question.trim().length < 3}
          className="shrink-0 rounded border border-accent-500/40 px-3 py-2 text-xs text-accent-400 transition-colors hover:bg-accent-500/10 disabled:opacity-40"
        >
          {loading ? "…" : "Ask"}
        </button>
      </form>

      {error && (
        <p className="mt-2.5 rounded border border-danger-400/30 bg-danger-400/10 px-3 py-2 text-xs text-danger-400">
          {error}
        </p>
      )}

      {loading && (
        <p className="mt-3 text-xs text-ink-400">Retrieving evidence and answering…</p>
      )}

      {result && !loading && (
        <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1">
          <div
            className={`rounded-lg border px-3 py-2.5 ${
              result.refused
                ? "border-warn-400/30 bg-warn-400/5"
                : "border-ink-800 bg-ink-900"
            }`}
          >
            <p className="text-xs leading-6 text-ink-100">
              <AnswerText
                text={result.answer}
                citations={result.citations}
                onSeek={onSeek}
              />
            </p>

            {result.refused && result.refusal_reason && (
              <p className="mt-2 text-[10px] text-warn-400/80">
                {result.refusal_reason}
              </p>
            )}
          </div>

          {result.citations.length > 0 && (
            <ul className="mt-2 space-y-1">
              {result.citations.map((c) => (
                <li key={c.marker}>
                  <CitationRow citation={c} showVideo={!videoId} onSeek={onSeek} />
                </li>
              ))}
            </ul>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10px] text-ink-600">
            <span className="tabular">{result.took_ms.toFixed(0)}ms</span>
            {result.model && <span>{result.model}</span>}
            {result.uncited_sentences > 0 && (
              <span title="Sentences removed for carrying no citation">
                {result.uncited_sentences}/{result.total_sentences} unsupported, removed
              </span>
            )}
            {result.fabricated_citations.length > 0 && (
              <span className="text-danger-400" title="Rejected before display">
                {result.fabricated_citations.length} invented citation
                {result.fabricated_citations.length === 1 ? "" : "s"} rejected
              </span>
            )}
            {result.evidence.length > 0 && (
              <button
                onClick={() => setShowEvidence((v) => !v)}
                className="underline-offset-2 hover:text-ink-400 hover:underline"
              >
                {showEvidence ? "hide" : "show"} all {result.evidence.length} considered
              </button>
            )}
          </div>

          {showEvidence && (
            <ul className="mt-2 space-y-1">
              {result.evidence.map((e) => (
                <li
                  key={e.marker}
                  className="rounded border border-ink-800 bg-ink-950 px-2.5 py-1.5"
                >
                  <div className="flex items-baseline gap-2">
                    <span className="tabular shrink-0 text-[10px] text-ink-600">
                      c_{e.marker}
                    </span>
                    <button
                      onClick={() => onSeek?.(e.start_s)}
                      className="tabular text-[10px] text-accent-400"
                    >
                      {formatTimestamp(e.start_s)}
                    </button>
                    {e.relevance != null && (
                      <span className="tabular ml-auto text-[10px] text-ink-600">
                        {e.relevance.toFixed(1)}
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-ink-400">
                    {e.text}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** Render `[c_N]` markers as clickable timestamps. */
function AnswerText({
  text,
  citations,
  onSeek,
}: {
  text: string;
  citations: Citation[];
  onSeek?: (seconds: number) => void;
}) {
  const byMarker = new Map(citations.map((c) => [c.marker, c]));
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;

  for (const match of text.matchAll(CITATION_RE)) {
    const index = match.index ?? 0;
    if (index > cursor) parts.push(text.slice(cursor, index));

    const citation = byMarker.get(Number(match[1]));
    if (citation) {
      parts.push(
        <CitationChip key={`c${key++}`} citation={citation} onSeek={onSeek} />,
      );
    }
    // A marker with no matching citation was already rejected server-side;
    // drop it rather than rendering a link that goes nowhere.
    cursor = index + match[0].length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));

  return <>{parts}</>;
}

function CitationChip({
  citation,
  onSeek,
}: {
  citation: Citation;
  onSeek?: (seconds: number) => void;
}) {
  const label = formatTimestamp(citation.start_s);
  const title = `${citation.video_title} — ${citation.text.slice(0, 160)}`;

  const className =
    "tabular mx-0.5 rounded bg-accent-500/15 px-1 py-px align-baseline text-[10px] text-accent-400 hover:bg-accent-500/25";

  return onSeek ? (
    <button onClick={() => onSeek(citation.start_s)} title={title} className={className}>
      {label}
    </button>
  ) : (
    <Link
      href={`/videos/${citation.video_id}?t=${Math.floor(citation.start_s)}`}
      title={title}
      className={className}
    >
      {label}
    </Link>
  );
}

function CitationRow({
  citation,
  showVideo,
  onSeek,
}: {
  citation: Citation;
  showVideo: boolean;
  onSeek?: (seconds: number) => void;
}) {
  const body = (
    <>
      <div className="flex items-baseline gap-2">
        <span className="tabular shrink-0 text-[10px] text-accent-400">
          {formatTimestamp(citation.start_s)}
        </span>
        {showVideo && (
          <span className="truncate text-[10px] text-ink-500">{citation.video_title}</span>
        )}
      </div>
      <p className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-ink-400">{citation.text}</p>
    </>
  );

  const className =
    "block w-full rounded border border-ink-800 bg-ink-900 px-2.5 py-1.5 text-left transition-colors hover:border-ink-600";

  return onSeek ? (
    <button onClick={() => onSeek(citation.start_s)} className={className}>
      {body}
    </button>
  ) : (
    <Link
      href={`/videos/${citation.video_id}?t=${Math.floor(citation.start_s)}`}
      className={className}
    >
      {body}
    </Link>
  );
}
