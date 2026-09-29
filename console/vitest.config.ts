/**
 * Test configuration, kept apart from `vite.config.ts` for the same reason
 * the other two interfaces keep them apart: the test run should not start a
 * proxy and the dev server should not load jsdom.
 *
 * The thresholds are 100%, matching the rest of this repository. What this
 * application shows is what a person decides the agent did wrong from, so a
 * verdict rendered by an untested branch is a diagnosis nobody checked.
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
