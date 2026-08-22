import { describe, expect, it } from "vitest";
import { formatTimestamp, parseTimestamp } from "@/lib/format";

/**
 * Timestamps are the unit this whole product trades in — every citation, every
 * search hit, every transcript row. A rendering bug here is visible everywhere
 * at once.
 */

describe("formatTimestamp", () => {
  it("omits the hour below an hour", () => {
    expect(formatTimestamp(0)).toBe("00:00");
    expect(formatTimestamp(27)).toBe("00:27");
    expect(formatTimestamp(605)).toBe("10:05");
  });

  it("shows hours past an hour, unpadded", () => {
    expect(formatTimestamp(3600)).toBe("1:00:00");
    expect(formatTimestamp(4575)).toBe("1:16:15");
    expect(formatTimestamp(20851)).toBe("5:47:31");
  });

  it("truncates rather than rounds", () => {
    // Rounding up would name a second the segment has not reached, and seeking
    // there can land past the line being cited.
    expect(formatTimestamp(26.9)).toBe("00:26");
  });

  it("degrades visibly rather than lying", () => {
    for (const bad of [null, undefined, NaN, Infinity, -1]) {
      expect(formatTimestamp(bad as number)).toBe("--:--");
    }
  });
});

describe("parseTimestamp", () => {
  it("round-trips what formatTimestamp produces", () => {
    for (const seconds of [0, 27, 605, 3600, 4575, 20851]) {
      expect(parseTimestamp(formatTimestamp(seconds))).toBe(seconds);
    }
  });

  it("accepts what a person actually types", () => {
    expect(parseTimestamp("1:12:30")).toBe(4350);
    expect(parseTimestamp("12:30")).toBe(750);
  });

  it("rejects nonsense instead of guessing", () => {
    for (const bad of ["", "abc", "not a time"]) {
      expect(parseTimestamp(bad)).toBeNull();
    }
  });
});
