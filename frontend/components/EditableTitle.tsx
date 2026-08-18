"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { Spinner } from "@/components/ui";

/**
 * A title you can rename in place.
 *
 * Uploaded filenames are rarely what you want to read later — a six-hour
 * course arriving as "videoplayback" is the normal case, not the exception.
 * The rename endpoint has existed since Phase 1; nothing ever called it.
 *
 * Edit-in-place rather than a separate dialog: renaming is a small, frequent,
 * low-risk action, and a modal for it costs two extra clicks and a context
 * switch. Enter commits, Escape reverts, blur commits — the conventions people
 * already expect from a filename field.
 */
export function EditableTitle({
  value,
  onSave,
}: {
  value: string;
  onSave: (title: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Guards against blur firing after Escape has already reverted.
  const committed = useRef(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (!editing) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [editing]);

  const commit = async () => {
    if (committed.current) return;
    committed.current = true;

    const next = draft.trim();
    if (!next || next === value) {
      setDraft(value);
      setEditing(false);
      setError(null);
      return;
    }

    setSaving(true);
    try {
      await onSave(next);
      setEditing(false);
      setError(null);
    } catch (err) {
      // Keep the field open with the attempted text: retyping a long title
      // because the save failed is a needless punishment.
      setError(err instanceof Error ? err.message : "Could not rename.");
      committed.current = false;
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    committed.current = true;
    setDraft(value);
    setEditing(false);
    setError(null);
  };

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => {
          committed.current = false;
          setEditing(true);
        }}
        title="Rename"
        className="group flex min-w-0 cursor-text items-center gap-1.5 rounded text-left"
      >
        <h1 className="truncate text-xl font-semibold tracking-tight text-ink-50">{value}</h1>
        <Icon
          name="type"
          size={14}
          className="shrink-0 text-ink-700 opacity-0 transition-opacity group-hover:opacity-100"
        />
      </button>
    );
  }

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          value={draft}
          disabled={saving}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void commit()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void commit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancel();
            }
          }}
          maxLength={512}
          aria-label="Video title"
          className="min-w-0 flex-1 rounded-md border border-accent-600 bg-ink-900 px-2 py-1 text-xl font-semibold tracking-tight text-ink-50 focus:outline-none"
        />
        {saving && <Spinner size={15} className="shrink-0 text-accent-400" />}
      </div>
      {error ? (
        <p role="alert" className="mt-1 text-xs text-danger-400">
          {error}
        </p>
      ) : (
        <p className="mt-1 text-2xs text-ink-500">Enter to save · Escape to cancel</p>
      )}
    </div>
  );
}
