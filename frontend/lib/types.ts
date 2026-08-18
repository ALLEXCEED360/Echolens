/** Mirrors backend/app/schemas.py. Keep in sync. */

export type VideoStatus = "uploading" | "uploaded" | "processing" | "ready" | "failed";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type StageStatus = "waiting" | "running" | "succeeded" | "failed" | "skipped";

export interface VideoSummary {
  /** First keyframe, when the visual stage has run. Null is a normal state. */
  poster_url?: string | null;
  id: string;
  title: string;
  status: VideoStatus;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  size_bytes: number;
  has_audio: boolean;
  created_at: string;
}

export interface VideoDetail extends VideoSummary {
  description: string | null;
  original_filename: string;
  mime_type: string;
  checksum_sha256: string | null;
  fps: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  audio_channels: number | null;
  audio_sample_rate: number | null;
  error: string | null;
  updated_at: string;
}

export interface VideoList {
  items: VideoSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface JobStage {
  name: string;
  position: number;
  status: StageStatus;
  progress: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  metrics: Record<string, unknown> | null;
}

export interface Job {
  id: string;
  video_id: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: number;
  stages: JobStage[];
}

export interface TranscriptSegment {
  id: string;
  position: number;
  start_s: number;
  end_s: number;
  text: string;
  speaker_id: string | null;
  avg_logprob: number | null;
  no_speech_prob: number | null;
}

export interface Transcript {
  video_id: string;
  segments: TranscriptSegment[];
  total: number;
  model: string | null;
  speech_duration_s: number;
}

export interface TranscriptSearchHit {
  id: string;
  video_id: string;
  start_s: number;
  end_s: number;
  text: string;
  speaker_id: string | null;
}

export interface TranscriptSearchResults {
  query: string;
  hits: TranscriptSearchHit[];
  total: number;
}

export interface TemporalContext {
  keyframe_id: string | null;
  keyframe_time_s: number | null;
  /** OCR text from the frame on screen when this was said. */
  on_screen_text: string | null;
  events: { type: string; title: string; start_s: number }[];
  topic_title: string | null;
  topic_start_s: number | null;
}

export interface SearchHit {
  chunk_id: string;
  video_id: string;
  video_title: string;
  start_s: number;
  end_s: number;
  /** The child chunk (~18s) — what matched, precise about when. */
  text: string;
  /**
   * "transcript" (spoken) or "ocr" (machine-read from the screen).
   * OCR of a 360p code editor is a good *locator* but poor reading material,
   * so the UI must label it rather than present it as if someone said it.
   */
  kind: string;
  score: number;
  /** "semantic", "lexical", or both. */
  matched_by: string[];
  semantic_rank: number | null;
  lexical_rank: number | null;
  /** The enclosing parent (~70s) — wider context. */
  parent_text: string | null;
  parent_start_s: number | null;
  parent_end_s: number | null;
  /** Cross-encoder relevance. >2 is a genuine match, <0 means nothing fits. */
  rerank_score: number | null;
  /** Rank before reranking, so promotions are visible. */
  fused_rank: number | null;
  context: TemporalContext | null;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  total: number;
  semantic_candidates: number;
  lexical_candidates: number;
  fused_candidates: number;
  took_ms: number;
  embed_ms: number;
  rerank_ms: number | null;
  reranked: boolean;
  /** Top cross-encoder score — the "did we find anything" signal. */
  top_relevance: number | null;
}

export interface SearchStats {
  by_level: Record<string, { chunks: number; embedded: number }>;
  videos_indexed: number;
  searchable: boolean;
}

export interface OcrBlock {
  text: string;
  confidence: number;
  bbox: { x1: number; y1: number; x2: number; y2: number } | null;
}

export interface Keyframe {
  id: string;
  position: number;
  start_s: number;
  end_s: number;
  time_s: number;
  /** Hamming distance from the previous keyframe — how much changed here. */
  change: number;
  image_url: string;
  /** OCR blocks joined in reading order; empty when no usable text was found. */
  text: string;
  ocr_blocks: OcrBlock[];
}

export interface KeyframeList {
  video_id: string;
  items: Keyframe[];
  total: number;
}

export interface TimelineEvent {
  id: string;
  type: string;
  /** "rule" (deterministic) or "model" (from embedding structure). */
  source: string;
  start_s: number;
  end_s: number;
  title: string;
  confidence: number;
  evidence: Record<string, unknown> | null;
}

export interface EventList {
  video_id: string;
  items: TimelineEvent[];
  total: number;
  by_type: Record<string, number>;
}

export interface TopicNode {
  id: string;
  position: number;
  depth: number;
  start_s: number;
  end_s: number;
  title: string;
  keywords: string[];
  boundary_strength: number;
  children: TopicNode[];
}

export interface TopicTree {
  video_id: string;
  items: TopicNode[];
  total: number;
  coarse: number;
  fine: number;
}

/** Timeline marker colours, keyed by event type. */
export const EVENT_STYLES: Record<string, { label: string; className: string }> = {
  topic_change: { label: "Topic", className: "bg-accent-400" },
  scene_change: { label: "Scene", className: "bg-ink-300" },
  slide_change: { label: "Slide", className: "bg-ink-300" },
  text_appeared: { label: "Text", className: "bg-warn-400" },
  silence: { label: "Silence", className: "bg-ink-600" },
  speaker_change: { label: "Speaker", className: "bg-danger-400" },
};

export interface Citation {
  marker: number;
  chunk_id: string;
  video_id: string;
  video_title: string;
  start_s: number;
  end_s: number;
  text: string;
}

export interface AnswerEvidence extends Citation {
  on_screen_text: string | null;
  topic_title: string | null;
  relevance: number | null;
}

export interface AnswerResponse {
  question: string;
  /** Contains inline [c_N] markers; render them from `citations`. */
  answer: string;
  citations: Citation[];
  evidence: AnswerEvidence[];
  refused: boolean;
  refusal_reason: string | null;
  /** Markers the model invented, rejected before display. Should be empty. */
  fabricated_citations: number[];
  uncited_sentences: number;
  total_sentences: number;
  model: string | null;
  took_ms: number;
}

export interface CollectionSummary {
  id: string;
  name: string;
  description: string | null;
  video_count: number;
  /** How many of those videos actually have chunks — unprocessed ones are invisible to search. */
  indexed_count: number;
  total_duration_s: number;
  created_at: string;
}

export interface CollectionDetail extends CollectionSummary {
  videos: VideoSummary[];
}

export interface CollectionList {
  items: CollectionSummary[];
  total: number;
  unfiled_videos: number;
}

export interface Occurrence {
  chunk_id: string;
  start_s: number;
  end_s: number;
  text: string;
  relevance: number | null;
  topic_title: string | null;
}

export interface VideoTrack {
  video_id: string;
  video_title: string;
  duration_s: number | null;
  occurrences: Occurrence[];
}

export interface ConceptTimeline {
  query: string;
  tracks: VideoTrack[];
  total_occurrences: number;
  first_video_id: string | null;
  first_video_title: string | null;
  first_start_s: number | null;
  took_ms: number;
}

export interface Playability {
  playable: boolean;
  container: string;
  video_codec: string | null;
  audio_codec: string | null;
}

/** Human-readable stage labels, matching docs/02-pipeline.md. */
export const STAGE_LABELS: Record<string, string> = {
  probe: "Probe",
  audio_extract: "Audio extraction",
  transcribe: "Transcription",
  diarize: "Speaker ID",
  keyframes: "Keyframes",
  ocr: "OCR",
  caption: "Visual captions",
  events: "Events",
  embed: "Embeddings",
};

/** The phase each stage arrives in — so the UI can say "not built yet" honestly. */
export const STAGE_PHASE: Record<string, number> = {
  probe: 1,
  audio_extract: 2,
  transcribe: 2,
  diarize: 2,
  keyframes: 4,
  ocr: 4,
  caption: 4,
  events: 5,
  embed: 3,
};
