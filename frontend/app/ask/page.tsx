"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { Icon } from "@/components/ui/Icon";
import { PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import type { SearchStats } from "@/lib/types";

/**
 * `useSearchParams` opts a component into client-side rendering, and Next
 * refuses to statically prerender the page without a Suspense boundary around
 * it. The boundary is what lets the shell render while the params resolve.
 */
export default function AskPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center gap-2 py-24 text-sm text-ink-400">
          <Spinner /> Loading…
        </div>
      }
    >
      <AskPageInner />
    </Suspense>
  );
}

function AskPageInner() {
  const [stats, setStats] = useState<SearchStats | null>(null);
  // Collections link here with ?collection=<id> to scope the question.
  const collectionId = useSearchParams().get("collection") ?? undefined;

  useEffect(() => {
    api.searchStats().then(setStats).catch(() => setStats(null));
  }, []);

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col px-4 py-8 sm:px-6">
      <div className="shrink-0">
        <PageHeader
          title="Ask"
          subtitle="Answers come only from what your videos contain. Every claim carries a timestamp resolved from the database — click one to jump there."
        />
      </div>

      {stats && !stats.searchable && (
        <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-warn-400/30 bg-warn-950 px-4 py-3">
          <Icon name="alert" size={15} className="mt-0.5 shrink-0 text-warn-400" />
          <div>
            <p className="text-sm text-warn-300">Nothing is indexed yet. Upload a video and transcribe it first.</p>
            <Link
              href="/"
              className="mt-1 inline-flex items-center gap-1 text-xs text-ink-400 transition-colors hover:text-accent-400"
            >
              Go to library
              <Icon name="arrow-right" size={12} />
            </Link>
          </div>
        </div>
      )}

      <AskPanel collectionId={collectionId} />
    </div>
  );
}
