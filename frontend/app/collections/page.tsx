"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { formatTimestamp } from "@/lib/format";
import type { CollectionDetail, CollectionSummary, VideoSummary } from "@/lib/types";

/**
 * Collections — the unit cross-video questions are asked over.
 *
 * Membership is exclusive: assigning a video to a collection moves it. Deleting
 * a collection releases its videos rather than destroying them, which the UI
 * says explicitly, because "delete" on something containing six hours of
 * transcript deserves to be unambiguous.
 */
export default function CollectionsPage() {
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [unfiled, setUnfiled] = useState(0);
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [selected, setSelected] = useState<CollectionDetail | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [list, library] = await Promise.all([
        api.listCollections(),
        api.listVideos({ limit: 200 }),
      ]);
      setCollections(list.items);
      setUnfiled(list.unfiled_videos);
      setVideos(library.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load collections.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const open = useCallback(async (id: string) => {
    try {
      setSelected(await api.getCollection(id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not open that collection.");
    }
  }, []);

  const create = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      const created = await api.createCollection(trimmed);
      setName("");
      await load();
      setSelected(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create that collection.");
    }
  }, [name, load]);

  const toggleMembership = useCallback(
    async (videoId: string, isMember: boolean) => {
      if (!selected) return;
      try {
        const updated = isMember
          ? await api.removeFromCollection(selected.id, videoId)
          : await api.addToCollection(selected.id, videoId);
        setSelected(updated);
        await load();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not update membership.");
      }
    },
    [selected, load],
  );

  const remove = useCallback(async () => {
    if (!selected) return;
    if (!confirm(`Delete "${selected.name}"? Its ${selected.video_count} video(s) stay in your library.`)) {
      return;
    }
    try {
      await api.deleteCollection(selected.id);
      setSelected(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete that collection.");
    }
  }, [selected, load]);

  const memberIds = new Set(selected?.videos.map((v) => v.id) ?? []);

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight text-ink-50">Collections</h1>
        <p className="mt-1 text-sm text-ink-400">
          Group videos into a course or project, then search and ask questions scoped to just
          that group.
        </p>
      </div>

      {error && (
        <p className="mb-4 rounded border border-danger-400/30 bg-danger-400/10 px-3 py-2 text-xs text-danger-400">
          {error}
        </p>
      )}

      <div className="grid gap-6 md:grid-cols-[280px_1fr]">
        <div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void create();
            }}
            className="flex gap-2"
          >
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="New collection"
              className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-200 placeholder:text-ink-600"
            />
            <button
              type="submit"
              disabled={!name.trim()}
              className="shrink-0 rounded border border-accent-500/40 px-2.5 py-1.5 text-xs text-accent-400 disabled:opacity-40"
            >
              Add
            </button>
          </form>

          {loading && <p className="mt-4 text-xs text-ink-400">Loading…</p>}

          <ul className="mt-3 space-y-1">
            {collections.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => void open(c.id)}
                  className={`w-full rounded border px-2.5 py-2 text-left transition-colors ${
                    selected?.id === c.id
                      ? "border-accent-500/40 bg-accent-500/5"
                      : "border-ink-800 bg-ink-900 hover:border-ink-600"
                  }`}
                >
                  <p className="truncate text-xs text-ink-100">{c.name}</p>
                  <p className="tabular mt-0.5 text-[10px] text-ink-500">
                    {c.video_count} video{c.video_count === 1 ? "" : "s"}
                    {c.indexed_count < c.video_count && (
                      <span className="text-warn-400"> · {c.indexed_count} indexed</span>
                    )}
                    {c.total_duration_s > 0 && (
                      <> · {formatTimestamp(c.total_duration_s)}</>
                    )}
                  </p>
                </button>
              </li>
            ))}
          </ul>

          {!loading && collections.length === 0 && (
            <p className="mt-4 text-xs text-ink-600">No collections yet.</p>
          )}
          {unfiled > 0 && (
            <p className="mt-3 text-[10px] text-ink-600">
              {unfiled} video{unfiled === 1 ? "" : "s"} not in any collection
            </p>
          )}
        </div>

        <div>
          {selected ? (
            <>
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-sm font-medium text-ink-50">{selected.name}</h2>
                  <p className="tabular mt-0.5 text-[11px] text-ink-500">
                    {selected.video_count} video{selected.video_count === 1 ? "" : "s"} ·{" "}
                    {formatTimestamp(selected.total_duration_s)} total
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {selected.indexed_count > 0 && (
                    <>
                      <Link
                        href={`/ask?collection=${selected.id}`}
                        className="rounded border border-accent-500/40 px-2.5 py-1 text-xs text-accent-400 hover:bg-accent-500/10"
                      >
                        Ask
                      </Link>
                      <Link
                        href={`/search?collection=${selected.id}`}
                        className="rounded border border-ink-700 px-2.5 py-1 text-xs text-ink-300 hover:border-ink-600"
                      >
                        Search
                      </Link>
                    </>
                  )}
                  <button
                    onClick={() => void remove()}
                    className="rounded border border-ink-700 px-2.5 py-1 text-xs text-ink-400 hover:border-danger-400/50 hover:text-danger-400"
                  >
                    Delete
                  </button>
                </div>
              </div>

              <p className="mb-2 text-[10px] uppercase tracking-wider text-ink-600">
                Videos — click to add or remove
              </p>
              <ul className="space-y-1">
                {videos.map((v) => {
                  const isMember = memberIds.has(v.id);
                  return (
                    <li key={v.id}>
                      <button
                        onClick={() => void toggleMembership(v.id, isMember)}
                        className={`flex w-full items-center gap-3 rounded border px-3 py-2 text-left transition-colors ${
                          isMember
                            ? "border-accent-500/40 bg-accent-500/5"
                            : "border-ink-800 bg-ink-900 hover:border-ink-600"
                        }`}
                      >
                        <span
                          className={`shrink-0 text-xs ${
                            isMember ? "text-accent-400" : "text-ink-700"
                          }`}
                        >
                          {isMember ? "✓" : "+"}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-xs text-ink-200">
                          {v.title}
                        </span>
                        <span className="tabular shrink-0 text-[10px] text-ink-600">
                          {formatTimestamp(v.duration_s)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {videos.length === 0 && (
                <p className="text-xs text-ink-600">
                  No videos yet.{" "}
                  <Link href="/" className="text-accent-400">
                    Upload one
                  </Link>
                  .
                </p>
              )}
            </>
          ) : (
            <p className="text-xs text-ink-600">
              Select a collection to manage its videos.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
