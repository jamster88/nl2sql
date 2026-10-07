/**
 * Test configuration, kept apart from `vite.config.ts` so the test run does
 * not start a proxy and the dev server does not load jsdom.
 *
 * The coverage thresholds are 100% on purpose, matching the Python side of
 * this repository: a threshold below 100 is a number nobody looks at, while
 * a failing build is read immediately.
 */

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { sharedPackage } from "../web/src/config.ts";

const shared = sharedPackage(import.meta.url);

export default defineConfig({
  plugins: [react()],
  resolve: shared.resolve,
  server: shared.server,
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // The shared package's own tests run here too, against this page's React
    // (V6-25): one copy of the code, held to every page's dependencies.
    include: ["test/**/*.test.{ts,tsx}", ...shared.tests],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}", ...shared.sources],
      allowExternal: true,
      // The entry point mounts React onto a DOM element that only exists in
      // a browser; it is covered by the container test that loads the built
      // page, not by jsdom.
      exclude: ["src/main.tsx"],
      reporter: ["text", "html", "lcov"],
      thresholds: {
        statements: 100,
        branches: 100,
        functions: 100,
        lines: 100,
      },
    },
  },
});
