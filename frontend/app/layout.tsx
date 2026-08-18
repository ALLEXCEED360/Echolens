import type { Metadata } from "next";
import { IBM_Plex_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { SiteNav } from "@/components/SiteNav";

/**
 * Fonts are self-hosted by `next/font`, not linked from Google's CDN.
 *
 * Two reasons: the CDN request is a render-blocking round trip to a third
 * party on every cold load, and `next/font` emits a matching size-adjusted
 * fallback so the page does not reflow when the real face arrives.
 */
const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "EchoLens",
  description: "Multimodal video intelligence and temporal search",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="flex min-h-screen flex-col bg-canvas">
        <SiteNav />
        <main className="min-h-0 flex-1">{children}</main>
      </body>
    </html>
  );
}
