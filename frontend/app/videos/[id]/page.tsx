"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { EditableTitle } from "@/components/EditableTitle";
import { KeyframeStrip } from "@/components/KeyframeStrip";
import { PipelinePanel } from "@/components/PipelinePanel";
import { ProcessMenu } from "@/components/ProcessMenu";
import { ProcessingBanner } from "@/components/ProcessingBanner";
import { SearchPanel } from "@/components/SearchPanel";
import { Timeline } from "@/components/Timeline";
import { TopicList } from "@/components/TopicList";
import { ExportMenu } from "@/components/ExportMenu";
import { TranscriptPanel } from "@/components/TranscriptPanel";
import { SeekBar, VideoPlayer, type VideoPlayerHandle } from "@/components/VideoPlayer";
import { Icon } from "@/components/ui/Icon";
import {
  Badge,
  Button,
  ConfirmDialog,
  DataRow,
  Disclosure,
  Dot,
  EmptyState,
  ErrorNote,
  Panel,
  PanelHeader,
  Spinner,
  StatusDot,
  Tabs,
} from "@/components/ui";
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

type RailTab = "ask" | "search" | "chapters" | "details";

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
  const [tab, setTab] = useState<RailTab>("ask");
  const [confirmDelete, setConfirmDelete] = useState(false);
  // Whether this video's speech sits under music and effects. Decides
  // voice-activity detection, which discards real dialogue on noisy audio
  // and prevents invented text on quiet audio — opposite needs, so it has
  // to be a choice about the media rather than a global setting.
  const [audio, setAudio] = useState<"clear" | "noisy">("clear");
  // Names and jargon to bias the decoder. Whisper renders unfamiliar
  // proper nouns as whatever sounds closest — "Harkov" came back as
  // "Raccoon" until it was given the cast list.
  const [vocabulary, setVocabulary] = useState("");
  const [deleting, setDeleting] = useState(false);

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
        setJob(await api.startProcessing(id, stages, { audio, vocabulary }));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Could not start processing.");
      } finally {
        setStarting(false);
      }
    },
    [id, audio, vocabulary],
  );

  const remove = useCallback(async () => {
    setDeleting(true);
    try {
      await api.deleteVideo(id);
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed.");
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
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
      <div className="mx-auto max-w-2xl px-6 py-16">
        <ErrorNote>{error}</ErrorNote>
        <Link
          href="/"
          className="mt-4 inline-flex items-center gap-1 text-xs text-ink-400 hover:text-accent-400"
        >
          <Icon name="chevron-right" size={13} className="rotate-180" />
          Back to library
        </Link>
      </div>
    );
  }

  if (!video) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-sm text-ink-400">
        <Spinner /> Loading video…
      </div>
    );
  }

  const transcribeStage = job?.stages.find((s) => s.name === "transcribe");
  const canProcess = !active && video.has_audio;
  const indexed = segments.length > 0;
  const framesWithText = keyframes.filter((k) => k.text).length;

  // How much of the runtime is actually speech.
  //
  // Whisper discards non-speech audio, so a gameplay clip of gunfire and music
  // legitimately yields almost nothing. Without this figure a correct result is
  // indistinguishable from a broken one, which is exactly the conclusion an
  // empty-looking transcript invites.
  const speechSeconds = segments.reduce((sum, s) => sum + (s.end_s - s.start_s), 0);
  const speechCoverage = video.duration_s ? speechSeconds / video.duration_s : 0;
  const sparseSpeech = indexed && speechCoverage < 0.35;

  return (
    <div className="flex h-full flex-col xl:flex-row">
      {/* Centre column scrolls; the transcript keeps its own bounded scroll.
          The virtualiser needs a container with a real height — `flex-1`
          inside a scrolling parent collapses to zero, rendering an empty list
          over a correctly-sized 198,750px spacer. An explicit height on the
          transcript panel gives it one without freezing the column, which
          previously pushed the transcript off-screen entirely on any display
          shorter than about 950px. */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto px-4 py-5 sm:px-6">
        <header className="mb-4">
          <Link
            href="/"
            className="inline-flex items-center gap-1 text-xs text-ink-400 transition-colors hover:text-accent-400"
          >
            <Icon name="chevron-right" size={13} className="rotate-180" />
            Library
          </Link>

          <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <EditableTitle
                value={video.title}
                onSave={async (title) => {
                  const updated = await api.updateVideo(id, { title });
                  setVideo(updated);
                }}
              />
              <div className="tabular mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-400">
                <StatusDot status={active ? "processing" : video.status} />
                <span>{formatTimestamp(video.duration_s)}</span>
                <Dot />
                <span>{formatResolution(video.width, video.height)}</span>
                <Dot />
                <span>{formatBytes(video.size_bytes)}</span>
                {indexed && (
                  <>
                    <Dot />
                    <span>{segments.length.toLocaleString()} segments</span>
                  </>
                )}
                {topics.length > 0 && (
                  <>
                    <Dot />
                    <span>{topics.length} chapters</span>
                  </>
                )}
              </div>
            </div>

            {/* One button that explains itself, plus the destructive one held
                apart by a rule so it cannot be hit by muscle memory. */}
            <div className="flex flex-wrap items-center justify-end gap-2">
              {!active && (
                <ProcessMenu
                  indexed={indexed}
                  hasAudio={video.has_audio}
                  busy={starting}
                  audio={audio}
                  onAudioChange={setAudio}
                  vocabulary={vocabulary}
                  onVocabularyChange={setVocabulary}
                  onRun={(stages) => void process(stages)}
                />
              )}
              <span className="h-5 w-px bg-line-strong" aria-hidden />
              <Button variant="danger" icon="trash" onClick={() => setConfirmDelete(true)}>
                Delete
              </Button>
            </div>
          </div>
        </header>

        {error && (
          <div className="mb-3">
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}

        <ProcessingBanner job={job} />

        <VideoPlayer
          ref={playerRef}
          videoId={video.id}
          playable={playability?.playable ?? true}
          onTimeUpdate={setCurrentTime}
        />

        <div className="mt-3">
          <SeekBar currentTime={currentTime} duration={video.duration_s} onSeek={seek} />
        </div>

        {events.length > 0 && video.duration_s ? (
          <Panel className="mt-4">
            <Timeline
              events={events}
              duration={video.duration_s}
              currentTime={currentTime}
              onSeek={seek}
            />
          </Panel>
        ) : null}

        {keyframes.length > 0 && (
          <Panel className="mt-3">
            <KeyframeStrip
              keyframes={keyframes}
              currentTime={currentTime}
              onSeek={seek}
              framesWithText={framesWithText}
            />
          </Panel>
        )}

        <Panel className="mt-3 flex h-[min(560px,60vh)] min-h-[320px] shrink-0 flex-col overflow-hidden">
          <PanelHeader
            title="Transcript"
            icon="quote"
            count={
              indexed
                ? `${segments.length.toLocaleString()} segments · ${formatTimestamp(
                    speechSeconds,
                  )} of speech`
                : undefined
            }
          >
            {sparseSpeech && (
              <Badge
                tone="warn"
                icon="info"
                title="Whisper only transcribes detected speech; music, effects and silence are skipped"
              >
                {Math.round(speechCoverage * 100)}% speech
              </Badge>
            )}
            {indexed && segments.length > 0 && <ExportMenu videoId={id} />}
          </PanelHeader>

          {sparseSpeech && (
            <p className="flex items-start gap-2 border-b border-line bg-warn-950/40 px-3.5 py-2 text-2xs leading-4 text-warn-300">
              <Icon name="info" size={13} className="mt-px shrink-0" />
              <span>
                Only {formatTimestamp(speechSeconds)} of this video&rsquo;s{" "}
                {formatTimestamp(video.duration_s)} was detected as speech. That is expected
                when the rest is music, effects or silence — Whisper skips those rather than
                guessing. But if there <em>is</em> dialogue it missed, it is probably buried
                under the noise: set the dropdown above to{" "}
                <strong className="text-warn-200">Noisy audio</strong> and re-run.
              </span>
            </p>
          )}
          {indexed ? (
            <TranscriptPanel
              segments={segments}
              currentTime={currentTime}
              onSeek={seek}
              video={{ id: video.id, title: video.title }}
            />
          ) : active ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 py-10 text-center">
              <Spinner size={18} className="text-accent-400" />
              <p className="text-sm text-ink-200">
                {transcribeStage?.status === "running"
                  ? `Transcribing — ${Math.round((transcribeStage.progress ?? 0) * 100)}%`
                  : "Processing…"}
              </p>
              <p className="max-w-xs text-xs leading-5 text-ink-500">
                Running Whisper large-v3 on the GPU. Roughly eight minutes per hour of video.
              </p>
            </div>
          ) : (
            <EmptyState
              icon="quote"
              title={video.has_audio ? "Not transcribed yet" : "No audio track"}
              hint={
                video.has_audio
                  ? "Transcribe this video to search it, ask questions about it, and jump to any moment."
                  : "There is no speech to transcribe. The visual pipeline still works."
              }
              action={
                canProcess ? (
                  <Button variant="primary" icon="sparkles" onClick={() => void process()}>
                    Transcribe
                  </Button>
                ) : undefined
              }
            />
          )}
        </Panel>
        <ConfirmDialog
          open={confirmDelete}
          busy={deleting}
          title={`Delete “${video.title}”?`}
          body={
            <>
              This removes the video file, its transcript, {keyframes.length.toLocaleString()}{" "}
              keyframes and everything indexed from it. It cannot be undone.
            </>
          }
          onConfirm={() => void remove()}
          onCancel={() => setConfirmDelete(false)}
        />
      </div>

      {/* Right rail. Tabbed rather than stacked: seven panels in one column
          meant the flagship Ask box got about eighty pixels of height. */}
      <aside className="flex w-full shrink-0 flex-col border-t border-line bg-surface xl:h-full xl:w-[360px] xl:border-l xl:border-t-0">
        <Tabs
          label="Video tools"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "ask", label: "Ask", icon: "sparkles" },
            { value: "search", label: "Search", icon: "search" },
            { value: "chapters", label: "Chapters", icon: "layers", badge: topics.length || undefined },
            {
              value: "details",
              label: "Details",
              icon: "info",
              // A failed stage must advertise itself. Buried behind a tab with
              // no marker, the only symptom is a feature quietly not working.
              badge: job?.status === "failed" ? <Icon name="alert" size={11} className="text-danger-400" /> : undefined,
            },
          ]}
        />

        <div className="min-h-0 flex-1 overflow-y-auto" role="tabpanel">
          {tab === "ask" &&
            (indexed ? (
              <div className="p-4">
                <AskPanel videoId={id} onSeek={seek} />
              </div>
            ) : (
              <EmptyState
                icon="sparkles"
                title="Nothing to ask yet"
                hint="Transcribe this video and you can ask questions answered with clickable timestamps."
              />
            ))}

          {tab === "search" &&
            (indexed ? (
              <div className="p-4">
                <SearchPanel videoId={id} onSeek={seek} />
              </div>
            ) : (
              <EmptyState
                icon="search"
                title="Nothing to search yet"
                hint="Transcribe this video to make every spoken moment searchable."
              />
            ))}

          {tab === "chapters" &&
            (topics.length > 0 ? (
              <TopicList topics={topics} currentTime={currentTime} onSeek={seek} />
            ) : (
              <EmptyState
                icon="layers"
                title="No chapters yet"
                hint="Chapters are derived from the transcript once the video is indexed."
              />
            ))}

          {tab === "details" && (
            <div>
              <PipelinePanel job={job} />

              {indexed && (
                <Disclosure title="Transcript" defaultOpen>
                  <dl>
                    <DataRow label="Segments" value={segments.length.toLocaleString()} />
                    <DataRow
                      label="Speech"
                      value={formatTimestamp(
                        segments.reduce((sum, s) => sum + (s.end_s - s.start_s), 0),
                      )}
                    />
                    <DataRow
                      label="Model"
                      value={String(transcribeStage?.metrics?.model ?? "—").replace(
                        "faster-whisper/",
                        "",
                      )}
                    />
                    {typeof transcribeStage?.metrics?.realtime_factor === "number" && (
                      <DataRow
                        label="Speed"
                        value={`${transcribeStage.metrics.realtime_factor}× realtime`}
                      />
                    )}
                  </dl>
                </Disclosure>
              )}

              {keyframes.length > 0 && (
                <Disclosure title="Visual">
                  <dl>
                    <DataRow label="Keyframes" value={keyframes.length.toLocaleString()} />
                    <DataRow
                      label="With text"
                      value={
                        framesWithText > 0 ? (
                          framesWithText.toLocaleString()
                        ) : (
                          <Badge tone="warn" icon="alert">
                            none
                          </Badge>
                        )
                      }
                    />
                    <DataRow label="Events" value={events.length.toLocaleString()} />
                  </dl>
                </Disclosure>
              )}

              <Disclosure title="Media">
                <dl>
                  <DataRow label="Duration" value={formatTimestamp(video.duration_s)} />
                  <DataRow
                    label="Resolution"
                    value={formatResolution(video.width, video.height)}
                  />
                  <DataRow
                    label="Frame rate"
                    value={video.fps ? `${video.fps.toFixed(2)} fps` : "—"}
                  />
                  <DataRow label="Size" value={formatBytes(video.size_bytes)} />
                  <DataRow label="Video codec" value={video.video_codec ?? "—"} />
                  <DataRow
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
              </Disclosure>

              <Disclosure title="Source">
                <dl>
                  <DataRow
                    label="Filename"
                    value={video.original_filename}
                    title={video.original_filename}
                  />
                  <DataRow label="Status" value={video.status} />
                  <DataRow
                    label="SHA-256"
                    mono
                    value={
                      video.checksum_sha256 ? `${video.checksum_sha256.slice(0, 16)}…` : "—"
                    }
                    title={video.checksum_sha256 ?? undefined}
                  />
                </dl>
              </Disclosure>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
