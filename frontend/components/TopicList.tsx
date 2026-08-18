"use client";

import { useMemo, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { formatTimestamp } from "@/lib/format";
import type { TopicNode } from "@/lib/types";

/**
 * Topic hierarchy — the chapter list the video never had.
 *
 * Coarse topics collapse by default: a 6-hour tutorial yields ~45 of them over
 * ~103 fine ones, and showing every leaf at once is a wall of text rather than
 * a navigation aid. The section containing the playhead auto-expands.
 *
 * The two levels are told apart by *weight and indent*, not by size alone.
 * Previously both were near-identical grey text a pixel apart in size, so 45
 * chapters read as one undifferentiated list.
 */

interface Props {
  topics: TopicNode[];
  currentTime: number;
  onSeek: (seconds: number) => void;
}

export function TopicList({ topics, currentTime, onSeek }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const activeId = useMemo(() => {
    const containing = topics.filter((t) => t.start_s <= currentTime && currentTime <= t.end_s);
    return containing.length ? containing[containing.length - 1].id : null;
  }, [topics, currentTime]);

  if (topics.length === 0) return null;

  return (
    <ul className="py-1.5">
      {topics.map((topic, index) => {
        const isActive = topic.id === activeId;
        const isOpen = expanded.has(topic.id) || isActive;
        const hasChildren = topic.children.length > 0;

        return (
          <li key={topic.id}>
            <div
              className={`group relative flex items-start gap-1 pr-2 transition-colors ${
                isActive ? "bg-accent-950/60" : "hover:bg-ink-850"
              }`}
            >
              {/* An accent rail marks the section you are inside — readable at a
                  glance while scrolling, unlike a subtle background tint. */}
              {isActive && (
                <span className="absolute inset-y-0 left-0 w-0.5 bg-accent-400" aria-hidden />
              )}

              <button
                type="button"
                onClick={() =>
                  setExpanded((prev) => {
                    const next = new Set(prev);
                    if (next.has(topic.id)) next.delete(topic.id);
                    else next.add(topic.id);
                    return next;
                  })
                }
                disabled={!hasChildren}
                aria-label={isOpen ? `Collapse ${topic.title}` : `Expand ${topic.title}`}
                aria-expanded={hasChildren ? isOpen : undefined}
                className="mt-2 ml-2 shrink-0 cursor-pointer text-ink-500 transition-colors hover:text-ink-200 disabled:invisible"
              >
                <Icon
                  name="chevron-right"
                  size={12}
                  className={`transition-transform duration-150 ${isOpen ? "rotate-90" : ""}`}
                />
              </button>

              <button
                type="button"
                onClick={() => onSeek(topic.start_s)}
                className="min-w-0 flex-1 cursor-pointer py-1.5 pr-1 text-left"
              >
                <span className="flex items-baseline gap-2">
                  <span
                    className={`tabular shrink-0 text-2xs ${
                      isActive ? "text-accent-400" : "text-ink-500"
                    }`}
                  >
                    {formatTimestamp(topic.start_s)}
                  </span>
                  <span
                    className={`text-sm font-medium leading-5 ${
                      isActive ? "text-ink-50" : "text-ink-100"
                    }`}
                  >
                    {topic.title}
                  </span>
                </span>
                {hasChildren && !isOpen && (
                  <span className="ml-[3.25rem] mt-0.5 block text-2xs text-ink-500">
                    {topic.children.length} sections
                  </span>
                )}
              </button>

              <span className="mt-2 shrink-0 text-2xs tabular text-ink-700">{index + 1}</span>
            </div>

            {isOpen && hasChildren && (
              <ul className="ml-[1.6rem] border-l border-line pl-1">
                {topic.children.map((child) => {
                  const childActive =
                    child.start_s <= currentTime && currentTime <= child.end_s;
                  return (
                    <li key={child.id}>
                      <button
                        type="button"
                        onClick={() => onSeek(child.start_s)}
                        className={`flex w-full cursor-pointer items-baseline gap-2 py-1 pl-2 pr-2 text-left transition-colors ${
                          childActive ? "bg-accent-950/60" : "hover:bg-ink-850"
                        }`}
                      >
                        <span
                          className={`tabular shrink-0 text-2xs ${
                            childActive ? "text-accent-400" : "text-ink-500"
                          }`}
                        >
                          {formatTimestamp(child.start_s)}
                        </span>
                        <span
                          className={`text-xs leading-5 ${
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
