"use client";

import { Icon, type IconName } from "@/components/ui/Icon";
import { Disclosure, ErrorNote, Spinner } from "@/components/ui";
import { STAGE_LABELS, STAGE_PHASE, type Job, type StageStatus } from "@/lib/types";

const CURRENT_PHASE = 4;

/**
 * Stage status.
 *
 * Icons rather than the glyph characters this used before (`✓ ◐ ✕ ○`): those
 * render at wildly different weights across fonts, and a screen reader
 * announces "check mark" or nothing at all depending on the platform. Each
 * status also has its own shape, so the meaning survives without colour.
 */
const MARKS: Record<StageStatus, { icon: IconName; className: string; label: string }> = {
  succeeded: { icon: "check", className: "text-accent-400", label: "Succeeded" },
  running: { icon: "refresh", className: "text-warn-400", label: "Running" },
  failed: { icon: "alert", className: "text-danger-400", label: "Failed" },
  skipped: { icon: "close", className: "text-ink-500", label: "Skipped" },
  waiting: { icon: "clock", className: "text-ink-500", label: "Waiting" },
};

export function PipelinePanel({ job }: { job: Job | null }) {
  const running = job?.status === "queued" || job?.status === "running";

  return (
    <Disclosure title="Pipeline" defaultOpen={Boolean(running || job?.error)}>
      {!job ? (
        <p className="py-1 text-xs text-ink-500">This video has not been processed yet.</p>
      ) : (
        <>
          <ul className="space-y-px">
            {job.stages.map((stage) => {
              const mark = MARKS[stage.status];
              const phase = STAGE_PHASE[stage.name] ?? 99;
              const notBuilt = phase > CURRENT_PHASE;

              return (
                <li key={stage.name} className="flex items-center gap-2.5 py-1">
                  {stage.status === "running" ? (
                    <Spinner size={13} className="shrink-0 text-warn-400" />
                  ) : (
                    <Icon
                      name={mark.icon}
                      size={13}
                      className={`shrink-0 ${mark.className}`}
                    />
                  )}
                  <span className="sr-only">{mark.label}:</span>
                  <span
                    className={`flex-1 truncate text-xs ${
                      notBuilt ? "text-ink-500" : "text-ink-200"
                    }`}
                  >
                    {STAGE_LABELS[stage.name] ?? stage.name}
                  </span>

                  {stage.status === "running" && (
                    <span className="tabular shrink-0 text-2xs text-warn-400">
                      {Math.round(stage.progress * 100)}%
                    </span>
                  )}
                  {/* Honest about what exists rather than showing a bar that
                      will never move. */}
                  {notBuilt && stage.status === "waiting" && (
                    <span className="shrink-0 text-2xs uppercase tracking-wide text-ink-500">
                      phase {phase}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>

          {stageProgressBar(job)}

          {job.error && (
            <div className="mt-3">
              <ErrorNote>{job.error}</ErrorNote>
            </div>
          )}
        </>
      )}
    </Disclosure>
  );
}

/** A single overall bar, shown only while there is genuine movement to report. */
function stageProgressBar(job: Job) {
  if (job.status !== "running" && job.status !== "queued") return null;
  const pct = Math.round((job.progress ?? 0) * 100);
  return (
    <div className="mt-3">
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Overall processing progress"
        className="h-1 overflow-hidden rounded-full bg-ink-800"
      >
        <div
          className="h-full rounded-full bg-accent-500 transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="tabular mt-1.5 text-2xs text-ink-500">{pct}% overall</p>
    </div>
  );
}
