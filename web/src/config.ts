/**
 * How a page's Vite and Vitest configuration take in this package (V6-25).
 *
 * The package is source, not a build: a page imports `@nl2sql/web/...`, which
 * resolves here, and compiles it with its own toolchain. Its dependencies --
 * React, the testing library -- are the page's own: `dedupe` resolves them
 * from the page's `node_modules`, so this directory needs none and a page
 * never holds two Reacts. The package's tests run in every page's suite, and
 * its sources count towards every page's coverage.
 */

import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** This package's root: the directory above `src`. */
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Everything this package imports that a page supplies. */
export const PEERS = ["react", "react-dom", "@testing-library/react", "@testing-library/user-event"];

export interface SharedPackage {
  resolve: { alias: Record<string, string>; dedupe: string[] };
  /** The dev server may read this package as well as the page. */
  server: { fs: { allow: string[] } };
  /** This package's tests, as globs relative to the page. */
  tests: string[];
  /** Its sources, for the page's coverage: absolute, as coverage matches a
   *  file outside the page only that way. */
  sources: string[];
}

/** The settings for the page whose configuration file is at `configUrl`. */
export function sharedPackage(configUrl: string): SharedPackage {
  const page = dirname(fileURLToPath(configUrl));
  const here = relative(page, ROOT).split("\\").join("/");
  return {
    resolve: { alias: { "@nl2sql/web": resolve(ROOT, "src") }, dedupe: [...PEERS] },
    server: { fs: { allow: [page, ROOT] } },
    tests: [`${here}/test/**/*.test.{ts,tsx}`],
    sources: [`${ROOT.split("\\").join("/")}/src/**/*.{ts,tsx}`],
  };
}
