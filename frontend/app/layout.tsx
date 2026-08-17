import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "EchoLens",
  description: "Multimodal video intelligence and temporal search",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-ink-800 bg-ink-900 px-5">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="text-lg" aria-hidden>
              🎥
            </span>
            <span className="text-[15px] font-semibold tracking-tight text-ink-50">EchoLens</span>
            <span className="ml-1.5 rounded border border-ink-700 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-ink-400">
              Phase 8
            </span>
          </Link>
          <nav className="flex items-center gap-4">
            <Link
              href="/ask"
              className="text-xs text-ink-400 transition-colors hover:text-ink-200"
            >
              Ask
            </Link>
            <Link
              href="/search"
              className="text-xs text-ink-400 transition-colors hover:text-ink-200"
            >
              Search
            </Link>
            <Link
              href="/timeline"
              className="text-xs text-ink-400 transition-colors hover:text-ink-200"
            >
              Timeline
            </Link>
            <Link
              href="/collections"
              className="text-xs text-ink-400 transition-colors hover:text-ink-200"
            >
              Collections
            </Link>
            <a
              href="http://localhost:8000/docs"
              target="_blank"
              rel="noreferrer"
              className="text-xs text-ink-400 transition-colors hover:text-ink-200"
            >
              API docs ↗
            </a>
          </nav>
        </header>
        <main className="min-h-0 flex-1">{children}</main>
      </body>
    </html>
  );
}
