"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { SearchPanel } from "@/components/SearchPanel";
import { api } from "@/lib/api";
import type { SearchStats } from "@/lib/types";

/**
 * `useSearchParams` opts a component into client-side rendering, and Next
 * refuses to statically prerender the page without a Suspense boundary around
 * it. The boundary is what lets the shell render while the params resolve.
 */
export default function SearchPage() {
  return (
    <Suspense fallback={<p className="px-6 py-8 text-xs text-ink-400">Loading…</p>}>
      <SearchPageInner />
    </Suspense>
  );
}

function SearchPageInner() {
  const [stats, setStats] = useState<SearchStats | null>(null);
  // Collections link here with ?collection=<id> to scope the query.
  const collectionId = useSearchParams().get("collection") ?? undefined;

  useEffect(() => {
    api.searchStats().then(setStats).catch(() => setStats(null));
  }, []);

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col px-6 py-8">
      <div className="mb-5 shrink-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink-50">Search</h1>
        <p className="mt-1 text-sm text-ink-400">
          Semantic and keyword retrieval across every indexed video. Click a result to jump to
          that moment.
        </p>
      </div>

      {stats && !stats.searchable && (
        <div className="mb-5 rounded-lg border border-warn-400/30 bg-warn-400/5 px-4 py-3">
          <p className="text-xs text-warn-400">
            Nothing is indexed yet. Upload a video and click Transcribe to make it searchable.
          </p>
          <Link href="/" className="mt-1.5 inline-block text-xs text-ink-400 hover:text-ink-200">
            → Go to library
          </Link>
        </div>
      )}

      <SearchPanel collectionId={collectionId} />

      {stats?.searchable && (
        <p className="tabular mt-4 shrink-0 text-[11px] text-ink-600">
          {stats.by_level.child?.embedded.toLocaleString() ?? 0} searchable chunks across{" "}
          {stats.videos_indexed} video{stats.videos_indexed === 1 ? "" : "s"}
        </p>
      )}
    </div>
  );
}
