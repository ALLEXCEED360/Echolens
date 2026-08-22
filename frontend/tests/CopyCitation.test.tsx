import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CopyCitation } from "@/components/CopyCitation";

/**
 * The copy control.
 *
 * Two things here are easy to break silently. The button is hidden by opacity
 * until its row is hovered — if that ever becomes `hidden` or `display:none`
 * it leaves the tab order and the feature disappears for keyboard users
 * without anyone noticing. And the clipboard write can be refused outright
 * outside a secure context, which must surface rather than look like success.
 */

const SOURCE = {
  text: "rigidBody is a component on the game object",
  videoTitle: "Unity 2D Crash Course",
  videoId: "86657e11-4460-415c-bd27-26db5da73ac4",
  startS: 4575,
};

function setClipboard(impl: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn(impl) },
    configurable: true,
  });
}

beforeEach(() => setClipboard(async () => {}));
afterEach(cleanup);

describe("CopyCitation", () => {
  it("copies the line, without attribution or link", async () => {
    const written: string[] = [];
    setClipboard(async (t) => void written.push(t));

    render(<CopyCitation source={SOURCE} />);
    screen.getByRole("button").click();
    await vi.waitFor(() => expect(written).toHaveLength(1));

    expect(written[0]).toBe(SOURCE.text);
  });

  it("stays in the tab order while hidden", () => {
    // Hidden by opacity, never by `hidden` or `display:none` - otherwise the
    // control vanishes for anyone navigating by keyboard.
    render(<CopyCitation source={SOURCE} />);
    const button = screen.getByRole("button");

    expect(button.className).toContain("opacity-0");
    expect(button.hasAttribute("hidden")).toBe(false);
    expect(button.className).not.toContain("invisible");
    expect(button.className).not.toContain("hidden");
  });

  it("has an accessible name, since it shows only an icon", () => {
    render(<CopyCitation source={SOURCE} />);
    expect(screen.getByRole("button", { name: "Copy quote" })).toBeTruthy();
  });

  it("takes a caller-supplied label", () => {
    render(<CopyCitation source={SOURCE} label="Copy this line" />);
    expect(screen.getByRole("button", { name: "Copy this line" })).toBeTruthy();
  });

  it("reports a refused clipboard rather than appearing to work", async () => {
    setClipboard(async () => {
      throw new Error("NotAllowedError");
    });
    // The execCommand fallback is the other half of that path; jsdom does not
    // implement it, so returning false here exercises the failure branch.
    Object.defineProperty(document, "execCommand", {
      value: () => false,
      configurable: true,
    });

    render(<CopyCitation source={SOURCE} />);
    const button = screen.getByRole("button");
    button.click();

    await vi.waitFor(() =>
      expect(button.getAttribute("title")).toBe("Could not reach the clipboard"),
    );
    expect(button.className).toContain("opacity-100");
  });

  it("becomes visible once it has something to report", async () => {
    render(<CopyCitation source={SOURCE} />);
    const button = screen.getByRole("button");
    expect(button.className).toContain("opacity-0");

    button.click();
    await vi.waitFor(() => expect(button.className).toContain("opacity-100"));
  });
});
