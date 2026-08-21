/**
 * The one place a citation becomes text.
 *
 * Three surfaces quote a moment — an answer's evidence, a search hit, a
 * transcript line — and each was one `slice()` away from inventing its own
 * format. A citation whose shape depends on where it was copied from is not a
 * citation; it is a screenshot in prose. So the shape lives here, once.
 */

export interface CitationSource {
  /** What was said, over exactly the span `startS` names. */
  text: string;
  videoTitle: string;
  videoId: string;
  startS: number;
  /** `"ocr"` marks text read off the screen rather than spoken. */
  kind?: "speech" | "ocr" | null;
}

/**
 * Callers pass the whole moment, not just the line.
 *
 * `formatCitation` currently reads only `text`, so the rest looks redundant —
 * it is not. This format has already been through three revisions (parent
 * passage to child chunk, then no deep link, then no attribution), and every
 * caller has the title and timestamp to hand anyway because it is rendering
 * them. Keeping them flowing in means the next revision is one line here
 * rather than an edit to four components.
 */

/** Collapse to a single line. A pasted quote should not carry the source's wrapping. */
function oneLine(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

/**
 * Just the line, on its own.
 *
 * Earlier versions wrapped it in curly quotes and followed it with
 * `— Video Title, 12:34`, and before that a deep link as well. All of it was
 * decoration around the only part anyone wanted: whoever pastes a quote knows
 * where they got it, and on localhost the link was dead to every reader but the
 * machine that produced it. The quotes went with the attribution — they existed
 * to separate the two, and there is nothing left to separate.
 *
 * Not truncated. A quote silently cut at 160 characters is a misquote and the
 * person pasting it cannot tell that it happened; the caller is expected to
 * pass the child chunk, which is already the right unit.
 */
export function formatCitation(source: CitationSource): string {
  return oneLine(source.text);
}
