import { describe, expect, it } from "vitest";
import { formatCitation } from "@/lib/citation";

/**
 * What lands on the clipboard.
 *
 * This format was revised three times in one sitting — parent passage to child
 * chunk, then the deep link removed, then the attribution — and each revision
 * was prompted by someone pasting the output and finding it wrong. The shape
 * is small enough to look obviously correct and has not been, so it is pinned
 * here.
 */

const moment = (text: string) => ({
  text,
  videoTitle: "Unity 2D Crash Course",
  videoId: "86657e11-4460-415c-bd27-26db5da73ac4",
  startS: 4575,
});

describe("formatCitation", () => {
  it("is the line and nothing else", () => {
    expect(formatCitation(moment("rigidBody is a component on the game object"))).toBe(
      "rigidBody is a component on the game object",
    );
  });

  it("carries no attribution, timestamp or link", () => {
    const out = formatCitation(moment("a line"));

    expect(out).not.toContain("Unity 2D Crash Course");
    expect(out).not.toContain("1:16:15");
    expect(out).not.toContain("http");
  });

  it("does not wrap the line in quotation marks", () => {
    // They existed only to separate the quote from the attribution beneath it.
    const out = formatCitation(moment("a line"));
    expect(out.startsWith("\u201c")).toBe(false);
    expect(out.endsWith("\u201d")).toBe(false);
  });

  it("collapses newlines, so a pasted quote is one line", () => {
    // OCR text arrives with the screen's line breaks in it.
    expect(formatCitation(moment("public class Player\n  private Rigidbody2D rb;"))).toBe(
      "public class Player private Rigidbody2D rb;",
    );
  });

  it("collapses runs of whitespace and trims the ends", () => {
    expect(formatCitation(moment("  spaced    out  \n\n  text  "))).toBe("spaced out text");
  });

  it("does not truncate a long passage", () => {
    // A quote silently cut at N characters is a misquote, and whoever pastes
    // it cannot tell that it happened.
    const long = "word ".repeat(400).trim();
    expect(formatCitation(moment(long))).toBe(long);
  });

  it("ignores the metadata the caller still passes", () => {
    // Callers deliberately pass the whole moment so the next revision to this
    // format is one line here rather than an edit to four components.
    const a = formatCitation({ ...moment("same line"), kind: "ocr", startS: 0 });
    const b = formatCitation({ ...moment("same line"), kind: "speech", startS: 9999 });
    expect(a).toBe(b);
  });

  it("survives an empty line without producing whitespace", () => {
    expect(formatCitation(moment("   "))).toBe("");
  });
});
