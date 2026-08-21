"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type CopyState = "idle" | "copied" | "error";

/**
 * Put text on the clipboard, and say whether it worked.
 *
 * `navigator.clipboard` is unavailable outside a secure context — which
 * includes this app served over plain HTTP from anything but localhost, the
 * exact case where someone shows the tool to a colleague over the LAN. The
 * `execCommand` fallback is deprecated and still the only thing that works
 * there, so it stays until it stops working.
 *
 * Failure is reported rather than swallowed: a copy button that silently does
 * nothing is worse than one that says it could not.
 */
export function useCopy(resetAfterMs = 1800) {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), []);

  const copy = useCallback(
    async (text: string) => {
      let ok = false;
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch {
        ok = legacyCopy(text);
      }
      setState(ok ? "copied" : "error");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setState("idle"), resetAfterMs);
      return ok;
    },
    [resetAfterMs],
  );

  return { state, copy };
}

function legacyCopy(text: string): boolean {
  try {
    const area = document.createElement("textarea");
    area.value = text;
    // Off-screen rather than `display: none` — a hidden element cannot be
    // selected, and an unselected one cannot be copied.
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:-9999px;opacity:0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
