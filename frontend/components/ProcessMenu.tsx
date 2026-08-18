"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { Button, Input, Select } from "@/components/ui";

/**
 * Processing options, grouped with the actions that use them.
 *
 * These lived as five bare controls in the page header: two settings sandwiched
 * between three buttons, with nothing to indicate that the settings configured
 * the buttons, and every explanation hidden in a `title` tooltip nobody hovers.
 * Deciding what "Analyse visuals" did versus "Re-run all" — or when to touch
 * the dropdown at all — required knowing the pipeline already.
 *
 * Grouping them behind one button costs a click and buys a place to actually
 * say what each choice does, next to the action it affects.
 */
export function ProcessMenu({
  indexed,
  hasAudio,
  busy,
  audio,
  onAudioChange,
  vocabulary,
  onVocabularyChange,
  onRun,
}: {
  indexed: boolean;
  hasAudio: boolean;
  busy: boolean;
  audio: "clear" | "noisy";
  onAudioChange: (value: "clear" | "noisy") => void;
  vocabulary: string;
  onVocabularyChange: (value: string) => void;
  onRun: (stages?: string) => void;
}) {
  const [open, setOpen] = useState(false);
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

  const run = (stages?: string) => {
    setOpen(false);
    onRun(stages);
  };

  return (
    <div ref={wrapper} className="relative">
      <Button
        variant={indexed ? "secondary" : "primary"}
        icon="sparkles"
        iconRight="chevron-down"
        disabled={busy}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        {busy ? "Starting…" : indexed ? "Process" : "Transcribe"}
      </Button>

      {open && (
        <div
          role="dialog"
          aria-label="Processing options"
          className="animate-fade-up absolute right-0 z-20 mt-2 w-[22rem] rounded-lg border border-line-strong bg-overlay p-4 shadow-pop"
        >
          {hasAudio ? (
            <>
              <fieldset className="mb-4">
                <label
                  htmlFor="audio-type"
                  className="mb-1.5 block text-sm font-medium text-ink-100"
                >
                  What is the audio like?
                </label>
                <Select
                  id="audio-type"
                  value={audio}
                  onChange={(e) => onAudioChange(e.target.value as "clear" | "noisy")}
                >
                  <option value="clear">Clear speech — lecture, screencast, interview</option>
                  <option value="noisy">Noisy — gameplay, film, music underneath</option>
                </Select>
                <p className="mt-1.5 text-xs leading-5 text-ink-400">
                  {audio === "clear"
                    ? "Skips non-speech, which stops the transcriber inventing lines over silence."
                    : "Transcribes everything. Recovers dialogue buried under effects, at the cost of the odd mishearing."}
                </p>
              </fieldset>

              <fieldset className="mb-4">
                <label
                  htmlFor="vocabulary"
                  className="mb-1.5 block text-sm font-medium text-ink-100"
                >
                  Names and jargon{" "}
                  <span className="font-normal text-ink-500">(optional)</span>
                </label>
                <Input
                  id="vocabulary"
                  value={vocabulary}
                  onChange={(e) => onVocabularyChange(e.target.value)}
                  placeholder="Makarov, Rigidbody2D, Vorshevsky"
                />
                <p className="mt-1.5 text-xs leading-5 text-ink-400">
                  Unfamiliar names come out as whatever sounds closest — “Harkov” became
                  “Raccoon”. List them here and they will be recognised.
                </p>
              </fieldset>
            </>
          ) : (
            <p className="mb-4 flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2 text-xs leading-5 text-ink-400">
              <Icon name="info" size={14} className="mt-0.5 shrink-0 text-ink-500" />
              This video has no audio track, so there is nothing to transcribe. The visual
              pipeline still works.
            </p>
          )}

          <div className="space-y-2 border-t border-line pt-3">
            {hasAudio && (
              <button
                type="button"
                onClick={() => run()}
                className="w-full cursor-pointer rounded-md bg-accent-500 px-3 py-2 text-left transition-colors hover:bg-accent-400"
              >
                <span className="flex items-center gap-1.5 text-sm font-semibold text-ink-950">
                  <Icon name="sparkles" size={14} />
                  {indexed ? "Re-do everything" : "Transcribe and index"}
                </span>
                <span className="mt-0.5 block text-xs text-ink-950/70">
                  Speech, visuals and search index. Roughly 8 minutes per hour of video.
                </span>
              </button>
            )}

            <button
              type="button"
              onClick={() => run("visual")}
              className="w-full cursor-pointer rounded-md border border-line-strong bg-ink-750 px-3 py-2 text-left transition-colors hover:bg-ink-700"
            >
              <span className="flex items-center gap-1.5 text-sm font-medium text-ink-50">
                <Icon name="image" size={14} />
                Visuals only
              </span>
              <span className="mt-0.5 block text-xs text-ink-400">
                Keyframes and on-screen text.{" "}
                {indexed ? "Keeps the existing transcript." : "No transcription."} Minutes,
                not tens of minutes.
              </span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
