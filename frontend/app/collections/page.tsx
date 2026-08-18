"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import {
  Badge,
  Button,
  ConfirmDialog,
  Dot,
  EmptyState,
  ErrorNote,
  Input,
  PageHeader,
} from "@/components/ui";
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
  const [confirmDelete, setConfirmDelete] = useState(false);

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
    setConfirmDelete(false);
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
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <PageHeader
        title="Collections"
        subtitle="Group videos into a course or project, then scope search and questions to just that group."
      />

      {error && (
        <div className="mb-4">
          <ErrorNote>{error}</ErrorNote>
        </div>
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
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label="New collection name"
              placeholder="New collection"
              className="min-w-0 flex-1"
            />
            <Button
              type="submit"
              variant="primary"
              icon="plus"
              disabled={!name.trim()}
              className="shrink-0"
            >
              Add
            </Button>
          </form>

          {loading && <p className="mt-4 text-xs text-ink-500">Loading…</p>}

          <ul className="mt-3 space-y-1">
            {collections.map((c) => (
              <li key={c.id}>
                <button
                  onClick={() => void open(c.id)}
                  className={`w-full rounded border px-2.5 py-2 text-left transition-colors ${
                    selected?.id === c.id
                      ? "border-accent-500/40 bg-accent-500/5"
                      : "border-line bg-surface hover:border-ink-600 hover:bg-raised"
                  }`}
                >
                  <p className="truncate text-xs text-ink-100">{c.name}</p>
                  <p className="tabular mt-0.5 text-2xs text-ink-500">
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
            <p className="mt-4 text-xs text-ink-500">No collections yet.</p>
          )}
          {unfiled > 0 && (
            <p className="mt-3 text-2xs text-ink-500">
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
                  <p className="tabular mt-0.5 text-2xs text-ink-500">
                    {selected.video_count} video{selected.video_count === 1 ? "" : "s"} ·{" "}
                    {formatTimestamp(selected.total_duration_s)} total
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {selected.indexed_count > 0 && (
                    <>
                      <Link
                        href={`/ask?collection=${selected.id}`}
                        className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md bg-accent-500 px-2.5 text-xs font-semibold text-ink-950 transition-colors duration-150 hover:bg-accent-400"
                      >
                        <Icon name="sparkles" size={13} />
                        Ask
                      </Link>
                      <Link
                        href={`/search?collection=${selected.id}`}
                        className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-line-strong bg-ink-750 px-2.5 text-xs font-medium text-ink-100 transition-colors duration-150 hover:bg-ink-700"
                      >
                        <Icon name="search" size={13} />
                        Search
                      </Link>
                    </>
                  )}
                  <span className="h-5 w-px bg-line-strong" aria-hidden />
                  <Button
                    size="sm"
                    variant="danger"
                    icon="trash"
                    onClick={() => setConfirmDelete(true)}
                  >
                    Delete
                  </Button>
                </div>
              </div>

              <p className="mb-2 text-2xs uppercase tracking-wider text-ink-500">
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
                            : "border-line bg-surface hover:border-ink-600 hover:bg-raised"
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
                        <span className="tabular shrink-0 text-2xs text-ink-500">
                          {formatTimestamp(v.duration_s)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {videos.length === 0 && (
                <p className="text-xs text-ink-500">
                  No videos yet.{" "}
                  <Link href="/" className="text-accent-400">
                    Upload one
                  </Link>
                  .
                </p>
              )}
            </>
          ) : (
            <p className="text-xs text-ink-500">
              Select a collection to manage its videos.
            </p>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={selected ? `Delete “${selected.name}”?` : "Delete collection?"}
        body={
          selected ? (
            <>
              The collection is removed, but its {selected.video_count} video
              {selected.video_count === 1 ? "" : "s"} stay in your library —
              they simply become unfiled.
            </>
          ) : undefined
        }
        confirmLabel="Delete collection"
        onConfirm={() => void remove()}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
