/**
 * Build and dev-server configuration.
 *
 * The proxy is here for the same three reasons as the web GUI's: the review
 * service's certificate is the API's self-signed one, the review token must
 * not be shipped to a browser, and a second origin turns every request into
 * a CORS negotiation. `npm run dev` puts a proxy in front, exactly as the
 * production image puts nginx in front.
 *
 * The paths list is shorter than the web GUI's because this application
 * talks to one service and that service streams nothing.
 */

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { sharedPackage } from "../../web/src/config.ts";
import { devProxies } from "../../web/src/vite.ts";

const shared = sharedPackage(import.meta.url);

/** Paths the review service owns. Everything else is this app's own asset. */
const API_PATHS = ["/v1", "/healthz", "/readyz", "/openapi.json"];

const env = process.env;
const target = env.NL2SQL_REVIEW_URL ?? "https://localhost:8444";

/**
 * The review service, its certificate unchecked by default on a developer's
 * own machine (in the image the equivalent is on), its token as the bearer, and
 * sign-in to the auth service -- the proxies every page's dev server builds
 * the same way (../../web/src/vite.ts).
 */
const proxies = devProxies({
  paths: API_PATHS,
  target,
  auth: env.NL2SQL_AUTH_URL ?? "https://localhost:8446",
  secure: (env.NL2SQL_REVIEW_TLS_VERIFY ?? "false").toLowerCase() === "true",
  token: env.REVIEW_TOKEN ?? "",
});

export default defineConfig({
  plugins: [react()],
  resolve: shared.resolve,
  server: {
    fs: shared.server.fs,
    port: Number(env.REVIEW_GUI_PORT ?? 5174),
    proxy: proxies,
  },
  preview: {
    port: Number(env.REVIEW_GUI_PORT ?? 5174),
    proxy: proxies,
  },
  build: { outDir: "dist", sourcemap: true },
});
