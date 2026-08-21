"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { TranscriptFormat } from "@/lib/types";

/**
 * Getting the transcript out.
 *
 * Everything this pipeline produces was, until now, reachable only from inside
 * this application. A transcript you cannot subtitle a video with, paste into
 * notes, or hand to someone else is worth less than one you can.
 *
 * The formats are named by what they are *for* rather than by their extension.
 * "SRT" tells you nothing unless you already knew; "subtitles for a video
 * player" tells you whether it is the one you want.
 */
const CHOICES: { format: TranscriptFormat; label: string; hint: string }[] = [
  {
    format: "srt",
    label: "Subtitles (.srt)",
    hint: "Drop next to a video file — VLC, Premiere and YouTube all read it.",
  },
  {
    format: "vtt",
    label: "Web subtitles (.vtt)",
    hint: "For an HTML <track> element, or players that reject SRT.",
  },
  {
    format: "md",
    label: "Timestamped notes (.md)",
    hint: "Every line links back to the moment it was said.",
  },
  {
    format: "txt",
    label: "Plain text (.txt)",
    hint: "Prose with no timestamps — for reading, or for pasting elsewhere.",
  },
];

export function ExportMenu({ videoId, disabled }: { videoId: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (!wrapper.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Reset the confirmation so a second copy still reads as an action.
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    setCopyError(null);
    try {
      const response = await fetch(api.transcriptExportUrl(videoId, "txt"));
      if (!response.ok) throw new Error(String(response.status));
      await navigator.clipboard.writeText(await response.text());
      setCopied(true);
    } catch {
      // The clipboard API is refused outright over plain HTTP on anything but
      // localhost, so this is a real state rather than a defensive branch.
      setCopyError("Could not copy — download the text file instead.");
    }
  };

  return (
    <div ref={wrapper} className="relative">
      <Button
        size="sm"
        icon="download"
        iconRight="chevron-down"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
      >
        Export
      </Button>

      {open && (
        <div
          role="menu"
          aria-label="Export transcript"
          className="animate-fade-up absolute right-0 z-20 mt-2 w-[19rem] overflow-hidden rounded-lg border border-line-strong bg-overlay p-1.5 shadow-pop"
        >
          {CHOICES.map(({ format, label, hint }) => (
            <a
              key={format}
              // A plain link, not a fetch: the browser streams the file
              // straight to disk and takes the filename from the server's
              // Content-Disposition header.
              href={api.transcriptExportUrl(videoId, format)}
              role="menuitem"
              onClick={() => setOpen(false)}
              className="block rounded-md px-2.5 py-2 text-left transition-colors hover:bg-raised focus-visible:bg-raised focus-visible:outline-none"
            >
              <span className="flex items-center gap-2 text-sm font-medium text-ink-100">
                <Icon name="download" size={13} className="text-ink-500" />
                {label}
              </span>
              <span className="mt-0.5 block pl-[21px] text-xs leading-4 text-ink-400">
                {hint}
              </span>
            </a>
          ))}

          <div className="my-1.5 border-t border-line" />

          <button
            type="button"
            role="menuitem"
            onClick={copy}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm font-medium text-ink-100 transition-colors hover:bg-raised focus-visible:bg-raised focus-visible:outline-none"
          >
            <Icon
              name={copied ? "check" : "quote"}
              size={13}
              className={copied ? "text-accent-400" : "text-ink-500"}
            />
            {copied ? "Copied" : "Copy full text"}
          </button>
          {copyError && (
            <p className="px-2.5 pb-1 pt-0.5 text-xs leading-4 text-warn-300">{copyError}</p>
          )}
        </div>
      )}
    </div>
  );
}
