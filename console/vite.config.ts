/**
 * Build and dev-server configuration.
 *
 * The proxy is here for the same three reasons as the other two interfaces':
 * the console presents the API's self-signed certificate, its token must
 * not be shipped to a browser, and a second origin turns every request into
 * a CORS negotiation -- which the console, unlike the API, does not offer
 * unless it is told to. `npm run dev` puts a proxy in front, exactly as the
 * production image puts nginx in front.
 */

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { sharedPackage } from "../web/src/config.ts";
import { devProxies } from "../web/src/vite.ts";

const shared = sharedPackage(import.meta.url);

/** Paths the console owns. Everything else is this app's own asset. */
const API_PATHS = ["/v1", "/healthz", "/readyz", "/openapi.json"];

const env = process.env;
const target = env.NL2SQL_CONSOLE_URL ?? "https://localhost:8445";

/**
 * The console, its certificate unchecked by default on a developer's
 * own machine (in the image the equivalent is on), its token as the bearer, and
 * sign-in to the auth service -- the proxies every page's dev server builds
 * the same way (../web/src/vite.ts).
 */
const proxies = devProxies({
  paths: API_PATHS,
  target,
  auth: env.NL2SQL_AUTH_URL ?? "https://localhost:8446",
  secure: (env.NL2SQL_CONSOLE_TLS_VERIFY ?? "false").toLowerCase() === "true",
  token: env.CONSOLE_TOKEN ?? "",
});

export default defineConfig({
  plugins: [react()],
  resolve: shared.resolve,
  server: {
    fs: shared.server.fs,
    port: Number(env.CONSOLE_GUI_PORT ?? 5175),
    proxy: proxies,
  },
  preview: {
    port: Number(env.CONSOLE_GUI_PORT ?? 5175),
    proxy: proxies,
  },
  build: { outDir: "dist", sourcemap: true },
});
