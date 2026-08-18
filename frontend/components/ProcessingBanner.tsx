"use client";

import { Icon } from "@/components/ui/Icon";
import { Spinner } from "@/components/ui";
import { STAGE_LABELS, type Job } from "@/lib/types";

/**
 * Live processing status, always visible.
 *
 * The stage list lives in the Details tab, which is the right home for it
 * once a job has finished — but it is the wrong home for a job still running.
 * Tucking progress behind a tab *and* a collapsed disclosure meant a user who
 * had just started a multi-minute GPU job had nothing to look at, and no way
 * to tell a slow stage from a stalled one.
 *
 * This sits above the player on every tab, and disappears the moment there is
 * nothing to report.
 */
export function ProcessingBanner({ job }: { job: Job | null }) {
  if (!job) return null;

  const active = job.status === "queued" || job.status === "running";
  const failed = job.status === "failed";
  if (!active && !failed) return null;

  const running = job.stages.find((s) => s.status === "running");
  const done = job.stages.filter((s) => s.status === "succeeded").length;
  const planned = job.stages.filter((s) => s.status !== "skipped").length;
  const overall = Math.round((job.progress ?? 0) * 100);

  if (failed) {
    const culprit = job.stages.find((s) => s.status === "failed");
    return (
      <div
        role="alert"
        className="mb-4 flex items-start gap-2.5 rounded-lg border border-danger-400/30 bg-danger-950 px-4 py-3"
      >
        <Icon name="alert" size={16} className="mt-0.5 shrink-0 text-danger-400" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-danger-300">
            Processing failed
            {culprit && ` during ${STAGE_LABELS[culprit.name] ?? culprit.name}`}
          </p>
          {(job.error || culprit?.error) && (
            <p className="mt-0.5 text-xs leading-5 text-danger-300/80">
              {job.error || culprit?.error}
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="mb-4 rounded-lg border border-line bg-raised px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <Spinner size={15} className="text-accent-400" />
        <p className="text-sm font-medium text-ink-50">
          {running
            ? `${STAGE_LABELS[running.name] ?? running.name}…`
            : job.status === "queued"
              ? "Queued"
              : "Processing…"}
        </p>
        <span className="tabular text-xs text-ink-500">
          stage {Math.min(done + 1, planned)} of {planned}
        </span>
        <span className="tabular ml-auto text-sm font-semibold text-accent-400">{overall}%</span>
      </div>

      <div
        role="progressbar"
        aria-valuenow={overall}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Processing progress"
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-800"
      >
        <div
          className="h-full rounded-full bg-accent-500 transition-[width] duration-500 ease-out"
          style={{ width: `${overall}%` }}
        />
      </div>

      {/* Per-stage progress, because the overall bar barely moves during a
          40-minute transcription and a stalled job looks identical to a slow
          one otherwise. */}
      {running && (
        <p className="tabular mt-1.5 text-2xs text-ink-500">
          {Math.round((running.progress ?? 0) * 100)}% of this stage
          {running.name === "transcribe" && " · Whisper large-v3 on the GPU, ~8 min per hour of video"}
          {running.name === "ocr" && " · reading on-screen text, CPU"}
        </p>
      )}
    </div>
  );
}
