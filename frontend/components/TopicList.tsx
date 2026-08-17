"use client";

import { useMemo, useState } from "react";
import { formatTimestamp } from "@/lib/format";
import type { TopicNode } from "@/lib/types";

/**
 * Topic hierarchy — the chapter list the video never had.
 *
 * Coarse topics collapse by default: a 6-hour tutorial yields ~45 of them over
 * ~103 fine ones, and showing every leaf at once is a wall of text rather than
 * a navigation aid. The section containing the playhead auto-expands.
 */

interface Props {
  topics: TopicNode[];
  currentTime: number;
  onSeek: (seconds: number) => void;
}

export function TopicList({ topics, currentTime, onSeek }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const activeId = useMemo(() => {
    const containing = topics.filter(
      (t) => t.start_s <= currentTime && currentTime <= t.end_s,
    );
    return containing.length ? containing[containing.length - 1].id : null;
  }, [topics, currentTime]);

  if (topics.length === 0) return null;

  return (
    <ul className="space-y-0.5">
      {topics.map((topic) => {
        const isActive = topic.id === activeId;
        const isOpen = expanded.has(topic.id) || isActive;
        const hasChildren = topic.children.length > 0;

        return (
          <li key={topic.id}>
            <div
              className={`flex items-start gap-1.5 rounded px-1.5 py-1 transition-colors ${
                isActive ? "bg-accent-500/10" : "hover:bg-ink-850"
              }`}
            >
              <button
                onClick={() =>
                  setExpanded((prev) => {
                    const next = new Set(prev);
                    next.has(topic.id) ? next.delete(topic.id) : next.add(topic.id);
                    return next;
                  })
                }
                disabled={!hasChildren}
                aria-label={isOpen ? "Collapse" : "Expand"}
                className="mt-0.5 w-3 shrink-0 text-[9px] text-ink-600 disabled:opacity-0"
              >
                {isOpen ? "▾" : "▸"}
              </button>

              <button
                onClick={() => onSeek(topic.start_s)}
                className="min-w-0 flex-1 text-left"
              >
                <span
                  className={`tabular mr-1.5 text-[10px] ${
                    isActive ? "text-accent-400" : "text-ink-600"
                  }`}
                >
                  {formatTimestamp(topic.start_s)}
                </span>
                <span className={`text-xs ${isActive ? "text-ink-50" : "text-ink-200"}`}>
                  {topic.title}
                </span>
              </button>
            </div>

            {isOpen && hasChildren && (
              <ul className="ml-4 border-l border-ink-800 pl-1.5">
                {topic.children.map((child) => {
                  const childActive =
                    child.start_s <= currentTime && currentTime <= child.end_s;
                  return (
                    <li key={child.id}>
                      <button
                        onClick={() => onSeek(child.start_s)}
                        className={`flex w-full gap-1.5 rounded px-1.5 py-0.5 text-left transition-colors ${
                          childActive ? "bg-accent-500/10" : "hover:bg-ink-850"
                        }`}
                      >
                        <span
                          className={`tabular shrink-0 text-[10px] leading-4 ${
                            childActive ? "text-accent-400" : "text-ink-600"
                          }`}
                        >
                          {formatTimestamp(child.start_s)}
                        </span>
                        <span
                          className={`text-[11px] leading-4 ${
                            childActive ? "text-ink-50" : "text-ink-400"
                          }`}
                        >
                          {child.title}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
