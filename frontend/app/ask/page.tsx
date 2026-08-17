"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { api } from "@/lib/api";
import type { SearchStats } from "@/lib/types";

/**
 * `useSearchParams` opts a component into client-side rendering, and Next
 * refuses to statically prerender the page without a Suspense boundary around
 * it. The boundary is what lets the shell render while the params resolve.
 */
export default function AskPage() {
  return (
    <Suspense fallback={<p className="px-6 py-8 text-xs text-ink-400">Loading…</p>}>
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
    <div className="mx-auto flex h-full max-w-3xl flex-col px-6 py-8">
      <div className="mb-5 shrink-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink-50">Ask</h1>
        <p className="mt-1 text-sm text-ink-400">
          Answers are built only from what your videos actually contain. Every claim carries a
          timestamp resolved from the database — click one to jump there.
        </p>
      </div>

      {stats && !stats.searchable && (
        <div className="mb-5 rounded-lg border border-warn-400/30 bg-warn-400/5 px-4 py-3">
          <p className="text-xs text-warn-400">
            Nothing is indexed yet. Upload a video and transcribe it first.
          </p>
          <Link href="/" className="mt-1.5 inline-block text-xs text-ink-400 hover:text-ink-200">
            → Go to library
          </Link>
        </div>
      )}

      <AskPanel collectionId={collectionId} />
    </div>
  );
}
