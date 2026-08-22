"use client";

import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { Button, ErrorNote, Skeleton } from "@/components/ui";
import { CopyCitation } from "@/components/CopyCitation";
import { Icon } from "@/components/ui/Icon";
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

/** Inline chips shown per claim before collapsing to a "+N" count. */
const MAX_INLINE_CITATIONS = 3;

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
        className="flex flex-col gap-2"
      >
        {/* A textarea, not a single-line input: real questions are longer than
            a search term, and a one-line box that scrolls sideways discourages
            asking a proper one. */}
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            // Enter submits; Shift+Enter adds a line. Standard for a prompt box.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask(question);
            }
          }}
          rows={3}
          aria-label="Your question"
          placeholder={
            videoId
              ? "What is a prefab and why would I use one?"
              : "Ask anything about your indexed videos…"
          }
          className="w-full resize-y rounded-md border border-line-strong bg-ink-900 px-3 py-2.5 text-sm leading-6 text-ink-100 placeholder:text-ink-500 transition-colors duration-150 hover:border-ink-600 focus:border-accent-600 focus:outline-none focus-visible:outline-2 focus-visible:outline-accent-400"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-2xs text-ink-500">
            Answers cite the moment they came from.
          </span>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            icon="sparkles"
            disabled={loading || question.trim().length < 3}
          >
            {loading ? "Asking…" : "Ask"}
          </Button>
        </div>
      </form>

      {error && (
        <div className="mt-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {/* Skeletons rather than a line of text: they reserve the space the
          answer will occupy, so nothing below jumps when it arrives. */}
      {loading && (
        <div className="mt-3 space-y-2" aria-live="polite" aria-busy="true">
          <span className="sr-only">Retrieving evidence and answering</span>
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-3.5 w-[92%]" />
          <Skeleton className="h-3.5 w-[70%]" />
          <Skeleton className="mt-3 h-11 w-full" />
        </div>
      )}

      {result && !loading && (
        <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1">
          <div
            className={`animate-fade-up rounded-lg border px-3.5 py-3 ${
              result.refused
                ? "border-warn-400/30 bg-warn-950"
                : "border-line bg-raised"
            }`}
          >
            {result.refused && (
              <p className="mb-1.5 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-warn-400">
                <Icon name="alert" size={12} />
                No answer in this corpus
              </p>
            )}
            <p className="text-sm leading-6 text-ink-100">
              <AnswerText
                text={result.answer}
                citations={result.citations}
                onSeek={onSeek}
              />
            </p>

            {result.refused && result.refusal_reason && (
              <p className="mt-2 text-2xs leading-4 text-warn-400/80">{result.refusal_reason}</p>
            )}
          </div>

          {result.citations.length > 0 && (
            <>
              <h3 className="mt-3.5 mb-1.5 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide text-ink-500">
                <Icon name="quote" size={11} />
                Evidence · {result.citations.length}
              </h3>
              <ul className="space-y-1">
              {result.citations.map((c) => (
                <li key={c.marker}>
                  <CitationRow citation={c} showVideo={!videoId} onSeek={onSeek} />
                </li>
              ))}
              </ul>
            </>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-x-2.5 gap-y-1 border-t border-line pt-2 text-2xs text-ink-500">
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
                  className="group rounded border border-ink-800 bg-ink-950 px-2.5 py-1.5"
                >
                  <div className="flex items-baseline gap-2">
                    <span className="tabular shrink-0 text-2xs text-ink-500">
                      c_{e.marker}
                    </span>
                    <button
                      onClick={() => onSeek?.(e.start_s)}
                      className="tabular text-2xs text-accent-400"
                    >
                      {formatTimestamp(e.start_s)}
                    </button>
                    {e.relevance != null && (
                      <span className="tabular ml-auto text-2xs text-ink-500">
                        {e.relevance.toFixed(1)}
                      </span>
                    )}
                    <CopyCitation
                      className={e.relevance == null ? "ml-auto" : ""}
                      source={{
                        text: e.quote || e.text,
                        videoTitle: e.video_title,
                        videoId: e.video_id,
                        startS: e.start_s,
                      }}
                    />
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-ink-400">
                    {e.quote || e.text}
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
  // Consecutive markers belong to one claim. Past a few, extra timestamps stop
  // informing and start obscuring the sentence they are attached to — the full
  // set is listed under Evidence directly below.
  let run = 0;
  let hidden = 0;

  for (const match of text.matchAll(CITATION_RE)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      if (hidden > 0) {
        parts.push(
          <span key={`m${key++}`} className="mx-0.5 text-2xs text-ink-500">
            +{hidden}
          </span>,
        );
        hidden = 0;
      }
      run = 0;
      parts.push(text.slice(cursor, index));
    }

    const citation = byMarker.get(Number(match[1]));
    if (citation) {
      if (run < MAX_INLINE_CITATIONS) {
        parts.push(
          <CitationChip key={`c${key++}`} citation={citation} onSeek={onSeek} />,
        );
      } else {
        hidden += 1;
      }
      run += 1;
    }
    // A marker with no matching citation was already rejected server-side;
    // drop it rather than rendering a link that goes nowhere.
    cursor = index + match[0].length;
  }
  if (hidden > 0) {
    parts.push(
      <span
        key={`m${key}`}
        title="More sources for this claim — all of them are listed under Evidence"
        className="mx-0.5 text-2xs text-ink-500"
      >
        +{hidden}
      </span>,
    );
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

  // Chips need real separation, not a 2px margin.
  //
  // These are tabular digits at small size, so several in a row read as one
  // continuous number — "00:2201:1600:04…" rather than three timestamps. A
  // border, a wider gap and a leading marker glyph make each one a distinct
  // object at a glance.
  const className =
    "tabular mx-1 inline-flex items-center gap-0.5 rounded border border-accent-600/40 " +
    "bg-accent-500/10 px-1.5 py-px align-baseline text-2xs text-accent-300 " +
    "transition-colors hover:border-accent-500 hover:bg-accent-500/25 cursor-pointer";

  const body = (
    <>
      <Icon name="clock" size={10} strokeWidth={2.25} className="opacity-70" />
      {label}
    </>
  );

  return onSeek ? (
    <button onClick={() => onSeek(citation.start_s)} title={title} className={className}>
      {body}
    </button>
  ) : (
    <Link
      href={`/videos/${citation.video_id}?t=${Math.floor(citation.start_s)}`}
      title={title}
      className={className}
    >
      {body}
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
        <span className="tabular shrink-0 text-2xs text-accent-400">
          {formatTimestamp(citation.start_s)}
        </span>
        {showVideo && (
          <span className="truncate text-2xs text-ink-500">{citation.video_title}</span>
        )}
      </div>
      <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-ink-400">
        {citation.quote || citation.text}
      </p>
    </>
  );

  const className = "block min-w-0 flex-1 text-left";

  return (
    <div className="group flex items-start gap-1 rounded border border-ink-800 bg-ink-900 py-1.5 pl-2.5 pr-1 transition-colors hover:border-ink-600">
      {onSeek ? (
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
      )}
      <CopyCitation
        source={{
          // `quote`, not `text`: the latter is the parent passage the model
          // read, which can run a minute either side of this timestamp.
          // Copying it attributes a paragraph to a moment inside it.
          text: citation.quote || citation.text,
          videoTitle: citation.video_title,
          videoId: citation.video_id,
          startS: citation.start_s,
        }}
      />
    </div>
  );
}
