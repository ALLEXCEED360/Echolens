import type {
  TranscriptFormat,
  AnswerResponse,
  CollectionDetail,
  CollectionList,
  ConceptTimeline,
  EventList,
  Job,
  KeyframeList,
  Playability,
  SearchResponse,
  SearchStats,
  TopicTree,
  Transcript,
  TranscriptSearchResults,
  VideoDetail,
  VideoList,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // fetch only rejects on network failure, which here almost always means
    // the API is not running. Say so, rather than surfacing "Failed to fetch".
    throw new ApiError(`Cannot reach the API at ${API_URL}. Is the backend running?`, 0);
  }

  if (!response.ok) {
    const detail = await response
      .json()
      .then((b) => b?.detail)
      .catch(() => null);
    throw new ApiError(detail ?? `Request failed (${response.status})`, response.status);
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  listVideos: (params: { limit?: number; offset?: number; q?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit != null) query.set("limit", String(params.limit));
    if (params.offset != null) query.set("offset", String(params.offset));
    if (params.q) query.set("q", params.q);
    const qs = query.toString();
    return request<VideoList>(`/api/videos${qs ? `?${qs}` : ""}`);
  },

  getVideo: (id: string) => request<VideoDetail>(`/api/videos/${id}`),

  updateVideo: (id: string, body: { title?: string; description?: string }) =>
    request<VideoDetail>(`/api/videos/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  deleteVideo: (id: string) => request<void>(`/api/videos/${id}`, { method: "DELETE" }),

  getLatestJob: (id: string) => request<Job>(`/api/videos/${id}/job`),

  getPlayability: (id: string) => request<Playability>(`/api/videos/${id}/playable`),

  /**
   * Queue processing. `stages` accepts a preset (all, visual, speech, index) or
   * a comma-separated list; unselected stages read their inputs from what is
   * already stored, so "visual" adds keyframes and OCR without re-transcribing.
   */
  startProcessing: (
    id: string,
    stages?: string,
    opts: { audio?: "clear" | "noisy"; vocabulary?: string } = {},
  ) => {
    const query = new URLSearchParams();
    if (stages) query.set("stages", stages);
    // Decide whether voice-activity detection runs, and bias the decoder toward
    // names it would otherwise guess at. Sent per request rather than read from
    // server config, so both belong to the video.
    if (opts.audio) query.set("audio", opts.audio);
    if (opts.vocabulary?.trim()) query.set("vocabulary", opts.vocabulary.trim());
    const qs = query.toString();
    return request<Job>(`/api/videos/${id}/process${qs ? `?${qs}` : ""}`, { method: "POST" });
  },

  getTranscript: (id: string) => request<Transcript>(`/api/videos/${id}/transcript`),

  /**
   * A direct download URL, not a fetch.
   *
   * The browser handles the transfer, so a 5,000-segment transcript never
   * passes through JavaScript memory, and the filename comes from the
   * server's `Content-Disposition` rather than being guessed here.
   */
  transcriptExportUrl: (id: string, format: TranscriptFormat) =>
    `${API_URL}/api/videos/${id}/transcript/export?format=${format}`,

  searchTranscripts: (q: string, videoId?: string) => {
    const query = new URLSearchParams({ q });
    if (videoId) query.set("video_id", videoId);
    return request<TranscriptSearchResults>(`/api/search/transcript?${query}`);
  },

  /** Hybrid semantic + keyword search across the indexed corpus. */
  search: (
    q: string,
    opts: {
      videoId?: string;
      collectionId?: string;
      limit?: number;
      kinds?: string;
      startS?: number;
      endS?: number;
      rerank?: boolean;
    } = {},
  ) => {
    const query = new URLSearchParams({ q });
    if (opts.videoId) query.set("video_id", opts.videoId);
    if (opts.collectionId) query.set("collection_id", opts.collectionId);
    if (opts.limit) query.set("limit", String(opts.limit));
    if (opts.kinds) query.set("kinds", opts.kinds);
    if (opts.startS != null) query.set("start_s", String(opts.startS));
    if (opts.endS != null) query.set("end_s", String(opts.endS));
    if (opts.rerank === false) query.set("rerank", "false");
    return request<SearchResponse>(`/api/search?${query}`);
  },

  searchStats: () => request<SearchStats>("/api/search/stats"),

  listCollections: () => request<CollectionList>("/api/collections"),
  getCollection: (id: string) => request<CollectionDetail>(`/api/collections/${id}`),
  createCollection: (name: string, description?: string) =>
    request<CollectionDetail>("/api/collections", {
      method: "POST",
      body: JSON.stringify({ name, description: description ?? null }),
    }),
  deleteCollection: (id: string) =>
    request<void>(`/api/collections/${id}`, { method: "DELETE" }),
  addToCollection: (collectionId: string, videoId: string) =>
    request<CollectionDetail>(`/api/collections/${collectionId}/videos/${videoId}`, {
      method: "PUT",
    }),
  removeFromCollection: (collectionId: string, videoId: string) =>
    request<CollectionDetail>(`/api/collections/${collectionId}/videos/${videoId}`, {
      method: "DELETE",
    }),

  /** Where a concept appears across the corpus, grouped by video and ordered by time. */
  conceptTimeline: (
    q: string,
    opts: { collectionId?: string; videoId?: string; minRelevance?: number } = {},
  ) => {
    const query = new URLSearchParams({ q });
    if (opts.collectionId) query.set("collection_id", opts.collectionId);
    // The endpoint has always accepted this; the client simply never sent it,
    // so scoping a trace to one video was unreachable from the UI.
    if (opts.videoId) query.set("video_id", opts.videoId);
    if (opts.minRelevance != null) query.set("min_relevance", String(opts.minRelevance));
    return request<ConceptTimeline>(`/api/search/timeline?${query}`);
  },

  /** Ask a question. Citations are resolved server-side from the database. */
  ask: (
    question: string,
    opts: { videoId?: string; collectionId?: string; kinds?: string } = {},
  ) =>
    request<AnswerResponse>("/api/ask", {
      method: "POST",
      body: JSON.stringify({
        question,
        video_id: opts.videoId ?? null,
        collection_id: opts.collectionId ?? null,
        kinds: opts.kinds ?? null,
      }),
    }),

  getKeyframes: (id: string, opts: { withTextOnly?: boolean; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (opts.withTextOnly) query.set("with_text_only", "true");
    if (opts.limit) query.set("limit", String(opts.limit));
    const qs = query.toString();
    return request<KeyframeList>(`/api/videos/${id}/keyframes${qs ? `?${qs}` : ""}`);
  },

  getEvents: (id: string, opts: { types?: string; minConfidence?: number } = {}) => {
    const query = new URLSearchParams();
    if (opts.types) query.set("types", opts.types);
    if (opts.minConfidence != null) query.set("min_confidence", String(opts.minConfidence));
    const qs = query.toString();
    return request<EventList>(`/api/videos/${id}/events${qs ? `?${qs}` : ""}`);
  },

  getTopics: (id: string) => request<TopicTree>(`/api/videos/${id}/topics`),

  /** Absolute URL for a keyframe JPEG (the API returns a relative path). */
  keyframeImageUrl: (path: string) => `${API_URL}${path}`,

  /** Direct URL for the `<video>` element — range requests must not be proxied. */
  streamUrl: (id: string) => `${API_URL}/api/videos/${id}/stream`,
};

export interface UploadHandle {
  promise: Promise<VideoDetail>;
  abort: () => void;
}

/**
 * Upload a file with progress reporting.
 *
 * XMLHttpRequest rather than fetch: `fetch` gives no upload progress events
 * without a ReadableStream request body, which needs HTTP/2 and full duplex
 * support. For a 2 GB lecture, a progress bar is not optional.
 */
export function uploadVideo(
  file: File,
  { title, onProgress }: { title?: string; onProgress?: (fraction: number) => void } = {},
): UploadHandle {
  const xhr = new XMLHttpRequest();

  const promise = new Promise<VideoDetail>((resolve, reject) => {
    const query = new URLSearchParams({ filename: file.name });
    if (title) query.set("title", title);

    xhr.open("POST", `${API_URL}/api/videos?${query}`);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");

    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    });

    xhr.addEventListener("load", () => {
      if (xhr.status === 201) {
        onProgress?.(1);
        resolve(JSON.parse(xhr.responseText) as VideoDetail);
        return;
      }
      let detail = `Upload failed (${xhr.status})`;
      try {
        detail = JSON.parse(xhr.responseText).detail ?? detail;
      } catch {
        /* non-JSON error body */
      }
      reject(new ApiError(detail, xhr.status));
    });

    xhr.addEventListener("error", () =>
      reject(new ApiError(`Cannot reach the API at ${API_URL}.`, 0)),
    );
    xhr.addEventListener("abort", () => reject(new ApiError("Upload cancelled", 0)));

    xhr.send(file);
  });

  return { promise, abort: () => xhr.abort() };
}
