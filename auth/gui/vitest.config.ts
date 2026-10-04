/**
 * Test configuration, kept apart from `vite.config.ts` as the other GUIs
 * keep theirs: the test run should not start a proxy and the dev server
 * should not load jsdom.
 *
 * The thresholds are 100%, matching the rest of this repository. This
 * application decides who may sign in to everything else, so a partially
 * tested path here is a partially tested lock.
 */

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["test/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx"],
      reporter: ["text", "html", "lcov"],
      thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 },
    },
  },
});
