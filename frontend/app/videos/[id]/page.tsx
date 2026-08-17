"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { KeyframeStrip } from "@/components/KeyframeStrip";
import { PipelinePanel, Section } from "@/components/PipelinePanel";
import { SearchPanel } from "@/components/SearchPanel";
import { Timeline } from "@/components/Timeline";
import { TopicList } from "@/components/TopicList";
import { TranscriptPanel } from "@/components/TranscriptPanel";
import { SeekBar, VideoPlayer, type VideoPlayerHandle } from "@/components/VideoPlayer";
import { ApiError, api } from "@/lib/api";
import { formatBytes, formatResolution, formatTimestamp } from "@/lib/format";
import type {
  Job,
  Keyframe,
  Playability,
  TimelineEvent,
  TopicNode,
  TranscriptSegment,
  VideoDetail,
} from "@/lib/types";

/** Poll while a job is active. Long enough not to hammer, short enough to feel live. */
const POLL_MS = 1500;

export default function VideoPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const playerRef = useRef<VideoPlayerHandle>(null);

  const [video, setVideo] = useState<VideoDetail | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [playability, setPlayability] = useState<Playability | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [keyframes, setKeyframes] = useState<Keyframe[]>([]);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [topics, setTopics] = useState<TopicNode[]>([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const loadTranscript = useCallback(async () => {
    try {
      const transcript = await api.getTranscript(id);
      setSegments(transcript.segments);
    } catch {
      /* no transcript yet */
    }
    try {
      const frames = await api.getKeyframes(id, { limit: 3000 });
      setKeyframes(frames.items);
    } catch {
      /* no keyframes yet */
    }
    try {
      const [timeline, tree] = await Promise.all([api.getEvents(id), api.getTopics(id)]);
      setEvents(timeline.items);
      setTopics(tree.items);
    } catch {
      /* no timeline yet */
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const [detail, play] = await Promise.all([api.getVideo(id), api.getPlayability(id)]);
        if (cancelled) return;
        setVideo(detail);
        setPlayability(play);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load this video.");
        }
        return;
      }

      try {
        const latest = await api.getLatestJob(id);
        if (!cancelled) setJob(latest);
      } catch {
        /* no job yet */
      }
      if (!cancelled) await loadTranscript();
    })();

    return () => {
      cancelled = true;
    };
  }, [id, loadTranscript]);

  // Poll only while there is something to watch, and stop as soon as it settles.
  const active = job?.status === "queued" || job?.status === "running";
  useEffect(() => {
    if (!active) return;

    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const latest = await api.getLatestJob(id);
        if (cancelled) return;
        setJob(latest);

        if (latest.status !== "queued" && latest.status !== "running") {
          await loadTranscript();
          setVideo(await api.getVideo(id));
        }
      } catch {
        /* transient */
      }
    }, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [active, id, loadTranscript]);

  const process = useCallback(
    async (stages?: string) => {
      setStarting(true);
      setError(null);
      try {
        setJob(await api.startProcessing(id, stages));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not start processing.");
      } finally {
        setStarting(false);
      }
    },
    [id],
  );

  const remove = useCallback(async () => {
    if (!confirm("Delete this video and its stored file?")) return;
    try {
      await api.deleteVideo(id);
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed.");
    }
  }, [id, router]);

  const seek = useCallback((seconds: number) => {
    playerRef.current?.seek(seconds);
    playerRef.current?.play();
  }, []);

  // Deep link from a search result: /videos/{id}?t=1234. Applied once the
  // player reports a duration, since seeking before metadata loads is ignored.
  const deepLink = searchParams.get("t");
  useEffect(() => {
    if (!deepLink || !video) return;
    const target = Number(deepLink);
    if (!Number.isFinite(target)) return;
    const timer = setTimeout(() => playerRef.current?.seek(target), 400);
    return () => clearTimeout(timer);
  }, [deepLink, video]);

  if (error && !video) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-16 text-center">
        <p className="text-sm text-danger-400">{error}</p>
        <Link href="/" className="mt-4 inline-block text-xs text-ink-400 hover:text-ink-200">
          ← Back to library
        </Link>
      </div>
    );
  }

  if (!video) return <p className="py-16 text-center text-xs text-ink-400">Loading…</p>;

  const transcribeStage = job?.stages.find((s) => s.name === "transcribe");
  const canProcess = !active && video.has_audio;

  return (
    <div className="flex h-full">
      {/* Centre: player above, transcript filling the rest.
          `overflow-hidden` rather than `overflow-y-auto` is load-bearing: the
          virtualised transcript needs a bounded scroll container, and `flex-1`
          inside a scrolling parent collapses to zero height — which renders an
          empty list over a correctly-sized 198,750px spacer. */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden px-6 py-6">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <Link href="/" className="text-xs text-ink-400 transition-colors hover:text-ink-200">
              ← Library
            </Link>
            <h1 className="mt-1.5 truncate text-lg font-semibold tracking-tight text-ink-50">
              {video.title}
            </h1>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {!active && segments.length > 0 && (
              // Transcription is the expensive stage. Once a transcript exists,
              // adding or refreshing the visual layer should not pay for it again.
              <button
                onClick={() => void process("visual")}
                disabled={starting}
                title="Keyframes, OCR and re-index — reuses the existing transcript"
                className="rounded border border-ink-700 px-2.5 py-1 text-xs text-ink-300 transition-colors hover:border-accent-500/40 hover:text-accent-400 disabled:opacity-50"
              >
                {starting ? "Starting…" : "Analyse visuals"}
              </button>
            )}
            {canProcess && (
              <button
                onClick={() => void process()}
                disabled={starting}
                title={
                  segments.length > 0
                    ? "Re-run everything, including a full re-transcription"
                    : "Transcribe and index this video"
                }
                className="rounded border border-accent-500/40 px-2.5 py-1 text-xs text-accent-400 transition-colors hover:bg-accent-500/10 disabled:opacity-50"
              >
                {starting ? "Starting…" : segments.length > 0 ? "Re-run all" : "Transcribe"}
              </button>
            )}
            <button
              onClick={() => void remove()}
              className="rounded border border-ink-700 px-2.5 py-1 text-xs text-ink-400 transition-colors hover:border-danger-400/50 hover:text-danger-400"
            >
              Delete
            </button>
          </div>
        </div>

        {error && (
          <p className="mb-3 rounded border border-danger-400/30 bg-danger-400/10 px-3 py-2 text-xs text-danger-400">
            {error}
          </p>
        )}

        <VideoPlayer
          ref={playerRef}
          videoId={video.id}
          playable={playability?.playable ?? true}
          onTimeUpdate={setCurrentTime}
        />

        <div className="mt-3">
          <SeekBar
            currentTime={currentTime}
            duration={video.duration_s}
            onSeek={seek}
          />
        </div>

        {events.length > 0 && video.duration_s ? (
          <div className="mt-3">
            <Timeline
              events={events}
              duration={video.duration_s}
              currentTime={currentTime}
              onSeek={seek}
            />
          </div>
        ) : null}

        {keyframes.length > 0 && (
          <div className="mt-4">
            <KeyframeStrip keyframes={keyframes} currentTime={currentTime} onSeek={seek} />
          </div>
        )}

        <div className="mt-4 flex min-h-[220px] flex-1 shrink flex-col">
          <h2 className="mb-2.5 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            Transcript
          </h2>

          {segments.length > 0 ? (
            <TranscriptPanel segments={segments} currentTime={currentTime} onSeek={seek} />
          ) : active ? (
            <div className="rounded-lg border border-ink-800 bg-ink-900 px-4 py-8 text-center">
              <p className="text-xs text-ink-300">
                {transcribeStage?.status === "running"
                  ? `Transcribing… ${Math.round((transcribeStage.progress ?? 0) * 100)}%`
                  : "Processing…"}
              </p>
              <p className="mt-1.5 text-[11px] text-ink-600">
                Running large-v3 on the GPU. Long videos take several minutes.
              </p>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-ink-800 px-4 py-8 text-center">
              <p className="text-xs text-ink-400">
                {video.has_audio
                  ? "No transcript yet. Click Transcribe to generate one."
                  : "This video has no audio track, so there is nothing to transcribe."}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Right: intelligence panel */}
      <aside className="w-80 shrink-0 overflow-y-auto border-l border-ink-800 bg-ink-900">
        <Section title="Ask about this video">
          {segments.length > 0 ? (
            <AskPanel videoId={id} onSeek={seek} />
          ) : (
            <p className="text-xs text-ink-600">
              Transcribe this video to ask questions about it.
            </p>
          )}
        </Section>

        <Section title="Search this video">
          {segments.length > 0 ? (
            <SearchPanel videoId={id} onSeek={seek} />
          ) : (
            <p className="text-xs text-ink-600">
              Transcribe this video to make it searchable.
            </p>
          )}
        </Section>

        {topics.length > 0 && (
          <Section title={`Topics (${topics.length})`}>
            <TopicList topics={topics} currentTime={currentTime} onSeek={seek} />
          </Section>
        )}

        <PipelinePanel job={job} />

        {segments.length > 0 && (
          <Section title="Transcript">
            <dl className="space-y-1.5">
              <Row label="Segments" value={String(segments.length)} />
              <Row
                label="Speech"
                value={formatTimestamp(
                  segments.reduce((sum, s) => sum + (s.end_s - s.start_s), 0),
                )}
              />
              <Row
                label="Model"
                value={String(
                  transcribeStage?.metrics?.model ?? "—",
                ).replace("faster-whisper/", "")}
              />
              {typeof transcribeStage?.metrics?.realtime_factor === "number" && (
                <Row
                  label="Speed"
                  value={`${transcribeStage.metrics.realtime_factor}× realtime`}
                />
              )}
            </dl>
          </Section>
        )}

        <Section title="Media">
          <dl className="space-y-1.5">
            <Row label="Duration" value={formatTimestamp(video.duration_s)} />
            <Row label="Resolution" value={formatResolution(video.width, video.height)} />
            <Row label="Frame rate" value={video.fps ? `${video.fps.toFixed(2)} fps` : "—"} />
            <Row label="Size" value={formatBytes(video.size_bytes)} />
            <Row label="Video codec" value={video.video_codec ?? "—"} />
            <Row
              label="Audio"
              value={
                video.has_audio
                  ? `${video.audio_codec ?? "?"} · ${video.audio_channels ?? "?"}ch · ${
                      video.audio_sample_rate
                        ? `${(video.audio_sample_rate / 1000).toFixed(1)}kHz`
                        : "?"
                    }`
                  : "none"
              }
            />
          </dl>
        </Section>

        <Section title="Source">
          <dl className="space-y-1.5">
            <Row label="Filename" value={video.original_filename} title={video.original_filename} />
            <Row label="Status" value={video.status} />
            <Row
              label="SHA-256"
              value={video.checksum_sha256 ? `${video.checksum_sha256.slice(0, 16)}…` : "—"}
              title={video.checksum_sha256 ?? undefined}
            />
          </dl>
        </Section>
      </aside>
    </div>
  );
}

function Row({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-xs text-ink-400">{label}</dt>
      <dd className="tabular truncate text-xs text-ink-200" title={title}>
        {value}
      </dd>
    </div>
  );
}
