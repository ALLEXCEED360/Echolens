"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EditableTitle } from "@/components/EditableTitle";
import { UploadZone } from "@/components/UploadZone";
import { Icon } from "@/components/ui/Icon";
import {
  Badge,
  ConfirmDialog,
  Dot,
  EmptyState,
  ErrorNote,
  Input,
  PageHeader,
  IconButton,
  Skeleton,
  StatusDot,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatBytes, formatRelativeDate, formatResolution, formatTimestamp } from "@/lib/format";
import type { VideoSummary } from "@/lib/types";

export default function LibraryPage() {
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Which row is being renamed, and which is pending deletion. Held here
  // rather than per row so there is exactly one dialog on the page instead of
  // one per video.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<VideoSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async (q: string) => {
    try {
      const data = await api.listVideos({ q: q || undefined, limit: 100 });
      setVideos(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the library.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Debounced so typing in the filter does not issue a request per keystroke.
    const timer = setTimeout(() => void load(query), query ? 250 : 0);
    return () => clearTimeout(timer);
  }, [query, load]);

  const rename = useCallback(async (id: string, title: string) => {
    const updated = await api.updateVideo(id, { title });
    setVideos((prev) => prev.map((v) => (v.id === id ? { ...v, title: updated.title } : v)));
  }, []);

  const remove = useCallback(async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await api.deleteVideo(pendingDelete.id);
      setVideos((prev) => prev.filter((v) => v.id !== pendingDelete.id));
      setPendingDelete(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete that video.");
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete]);

  const totalHours = useMemo(
    () => videos.reduce((sum, v) => sum + (v.duration_s ?? 0), 0) / 3600,
    [videos],
  );

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <PageHeader
        title="Library"
        subtitle={
          videos.length > 0 ? (
            <>
              <span>{videos.length} video{videos.length === 1 ? "" : "s"}</span>
              <Dot />
              <span className="tabular">{totalHours.toFixed(1)} h</span>
              <Dot />
              <span>{videos.filter((v) => v.status === "ready").length} indexed</span>
            </>
          ) : (
            "Upload a video to transcribe, index and search it."
          )
        }
      >
        {(videos.length > 0 || query) && (
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            icon="search"
            aria-label="Filter videos by title"
            placeholder="Filter by title"
            className="w-full sm:w-56"
          />
        )}
      </PageHeader>

      <UploadZone onUploaded={() => void load(query)} />

      <div className="mt-6">
        {error && <ErrorNote>{error}</ErrorNote>}

        {/* Skeleton cards rather than a "Loading…" line: they hold the space
            the real rows will occupy, so the page does not jump when they
            arrive. */}
        {!error && loading && (
          <ul className="space-y-2" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <li key={i} className="flex gap-4 rounded-lg border border-line bg-surface p-3">
                <Skeleton className="h-[76px] w-[135px] shrink-0" />
                <div className="flex-1 space-y-2 py-1">
                  <Skeleton className="h-4 w-1/3" />
                  <Skeleton className="h-3 w-2/3" />
                  <Skeleton className="h-3 w-1/4" />
                </div>
              </li>
            ))}
          </ul>
        )}

        {!error && !loading && videos.length === 0 && (
          <EmptyState
            icon="video"
            title={query ? `Nothing matching “${query}”` : "No videos yet"}
            hint={
              query
                ? "Try a shorter or different title fragment."
                : "Drop a file above to get started. EchoLens will transcribe it, read the text on screen, and make every moment searchable."
            }
          />
        )}

        <ul className="space-y-2">
          {videos.map((video) => {
            const editing = renaming === video.id;
            return (
              <li
                key={video.id}
                className="group relative flex gap-4 rounded-lg border border-line bg-surface p-3 transition-colors duration-150 hover:border-ink-600 hover:bg-raised"
              >
                {/* A stretched overlay rather than a link wrapping the row.
                    Buttons nested inside an anchor are invalid HTML and break
                    keyboard navigation, so the navigation target is a sibling
                    that covers the card and sits *below* the action cluster.
                    It is withdrawn while renaming, or clicking the input would
                    navigate away mid-edit. */}
                {!editing && (
                  <Link
                    href={`/videos/${video.id}`}
                    aria-label={`Open ${video.title}`}
                    className="absolute inset-0 z-0 rounded-lg"
                  />
                )}

                <div className="relative aspect-video w-[135px] shrink-0 overflow-hidden rounded-md border border-line bg-ink-950">
                  {video.poster_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={api.keyframeImageUrl(video.poster_url)}
                      alt=""
                      loading="lazy"
                      decoding="async"
                      className="h-full w-full object-cover opacity-85 transition-opacity duration-150 group-hover:opacity-100"
                    />
                  ) : (
                    <span className="flex h-full items-center justify-center text-ink-600">
                      <Icon name="video" size={20} />
                    </span>
                  )}
                  <span className="tabular absolute bottom-1 right-1 rounded bg-canvas/85 px-1 py-px text-2xs text-ink-100">
                    {formatTimestamp(video.duration_s)}
                  </span>
                </div>

                <div className="flex min-w-0 flex-1 flex-col justify-between py-0.5">
                  <div className="min-w-0">
                    {/* Above the overlay so the input is actually reachable. */}
                    <div className="relative z-10 min-w-0">
                      <EditableTitle
                        value={video.title}
                        variant="row"
                        editing={editing}
                        onEditingChange={(next) => setRenaming(next ? video.id : null)}
                        onSave={(title) => rename(video.id, title)}
                      />
                    </div>
                    <p className="tabular mt-1 flex flex-wrap items-center gap-x-2 text-xs text-ink-400">
                      <span>{formatResolution(video.width, video.height)}</span>
                      <Dot />
                      <span>{formatBytes(video.size_bytes)}</span>
                      <Dot />
                      <span>{formatRelativeDate(video.created_at)}</span>
                    </p>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <StatusDot status={video.status} />
                    {!video.has_audio && (
                      <Badge tone="warn" icon="alert">
                        no audio
                      </Badge>
                    )}
                  </div>
                </div>

                {/* Always rendered, not hover-only: a control that exists only
                    on hover is unreachable by touch and invisible to keyboard
                    users. It merely brightens on hover and focus. */}
                <div className="relative z-10 flex shrink-0 items-center gap-1 self-center">
                  <IconButton
                    name="pencil"
                    label={`Rename ${video.title}`}
                    size="sm"
                    className="text-ink-500 opacity-70 transition-opacity hover:opacity-100 focus-visible:opacity-100 group-hover:opacity-100"
                    onClick={() => setRenaming(video.id)}
                  />
                  <IconButton
                    name="trash"
                    label={`Delete ${video.title}`}
                    size="sm"
                    className="text-ink-500 opacity-70 transition-opacity hover:text-danger-400 hover:opacity-100 focus-visible:opacity-100 group-hover:opacity-100"
                    onClick={() => setPendingDelete(video)}
                  />
                  <Icon
                    name="chevron-right"
                    size={16}
                    className="ml-0.5 text-ink-600 transition-colors group-hover:text-accent-400"
                  />
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        busy={deleting}
        title={pendingDelete ? `Delete “${pendingDelete.title}”?` : "Delete video?"}
        body="This removes the video file, its transcript and everything indexed from it. It cannot be undone."
        onConfirm={() => void remove()}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
