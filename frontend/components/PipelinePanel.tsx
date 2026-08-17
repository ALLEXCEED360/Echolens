"use client";

import { STAGE_LABELS, STAGE_PHASE, type Job, type StageStatus } from "@/lib/types";

const CURRENT_PHASE = 4;

const MARKS: Record<StageStatus, { glyph: string; className: string }> = {
  succeeded: { glyph: "✓", className: "text-accent-400" },
  running: { glyph: "◐", className: "text-warn-400" },
  failed: { glyph: "✕", className: "text-danger-400" },
  skipped: { glyph: "–", className: "text-ink-600" },
  waiting: { glyph: "○", className: "text-ink-600" },
};

export function PipelinePanel({ job }: { job: Job | null }) {
  if (!job) {
    return (
      <Section title="Pipeline">
        <p className="text-xs text-ink-400">No processing job for this video.</p>
      </Section>
    );
  }

  return (
    <Section title="Pipeline">
      <ul className="space-y-0.5">
        {job.stages.map((stage) => {
          const mark = MARKS[stage.status];
          const phase = STAGE_PHASE[stage.name] ?? 99;
          const notBuilt = phase > CURRENT_PHASE;

          return (
            <li key={stage.name} className="flex items-center gap-2.5 py-1">
              <span className={`w-3 shrink-0 text-center text-xs ${mark.className}`}>
                {mark.glyph}
              </span>
              <span
                className={`flex-1 truncate text-xs ${notBuilt ? "text-ink-600" : "text-ink-200"}`}
              >
                {STAGE_LABELS[stage.name] ?? stage.name}
              </span>

              {stage.status === "running" && (
                <span className="tabular shrink-0 text-[11px] text-warn-400">
                  {Math.round(stage.progress * 100)}%
                </span>
              )}
              {/* Honest about what exists rather than showing a bar that will
                  never move. */}
              {notBuilt && stage.status === "waiting" && (
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-ink-600">
                  phase {phase}
                </span>
              )}
            </li>
          );
        })}
      </ul>

      {job.error && (
        <p className="mt-3 rounded border border-danger-400/30 bg-danger-400/10 px-2.5 py-2 text-xs text-danger-400">
          {job.error}
        </p>
      )}
    </Section>
  );
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-ink-800 px-4 py-4">
      <h2 className="mb-2.5 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
        {title}
      </h2>
      {children}
    </section>
  );
}
