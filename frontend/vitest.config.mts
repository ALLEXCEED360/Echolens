import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  // Component tests are written as JSX; without this they fail to parse.
  plugins: [react()],
  test: {
    // jsdom, not node: `useCopy` reaches for `navigator.clipboard` and, when
    // that is refused, builds a real <textarea> to fall back through.
    environment: "jsdom",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
  resolve: {
    // Mirrors the `@/*` path alias in tsconfig.json. Without it every import
    // under test resolves differently from how Next resolves it at runtime.
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
});
