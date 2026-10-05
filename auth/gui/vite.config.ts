/**
 * Build and dev-server configuration.
 *
 * Everything this page calls is the auth service's -- `/directory/` for the
 * people and groups, `/auth/` for signing in -- so `npm run dev` proxies both
 * there, as nginx does in the image. The session cookie then belongs to the
 * dev server's own origin, as it belongs to nginx's.
 */

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { sharedPackage } from "../../web/src/config.ts";
import { devProxies } from "../../web/src/vite.ts";

const shared = sharedPackage(import.meta.url);

const env = process.env;
const target = env.NL2SQL_AUTH_URL ?? "https://localhost:8446";

/**
 * The auth service, its certificate unchecked by default on a developer's
 * own machine (in the image the equivalent is on), and
 * sign-in to the auth service -- the proxies every page's dev server builds
 * the same way (../../web/src/vite.ts).
 */
const proxies = devProxies({
  paths: ["/directory"],
  target,
  // `/auth` is this same service: sign-in and the directory are one.
  auth: target,
  secure: (env.NL2SQL_AUTH_TLS_VERIFY ?? "false").toLowerCase() === "true",
});

export default defineConfig({
  plugins: [react()],
  resolve: shared.resolve,
  server: {
    fs: shared.server.fs,
    port: Number(env.DIRECTORY_GUI_PORT ?? 5177),
    proxy: proxies,
  },
  preview: {
    port: Number(env.DIRECTORY_GUI_PORT ?? 5177),
    proxy: proxies,
  },
  build: { outDir: "dist", sourcemap: true },
});
