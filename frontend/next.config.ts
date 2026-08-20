import type { NextConfig } from "next";

const config: NextConfig = {
  // The player hits the API directly rather than through a Next.js rewrite:
  // proxying multi-GB range requests through the Node server would add a hop
  // and buffer bytes for no benefit. CORS on the FastAPI side covers it.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  },

  // `next build` and `next dev` share .next/ by default, and a build run while
  // dev is live replaces chunks the dev server still references — which surfaces
  // as "Cannot find module './102.js'" and requires deleting .next to recover.
  // Set NEXT_DIST_DIR to build into a separate directory instead.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",

  // Dev-only badge, never present in a production build. It defaults to
  // bottom-left, which is exactly where the library rows and the transcript
  // list sit.
  devIndicators: { position: "bottom-right" },
};

export default config;
