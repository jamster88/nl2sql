/**
 * Build and dev-server configuration.
 *
 * Everything this page calls is the auth service's -- `/directory/` for the
 * people and groups, `/auth/` for signing in -- so `npm run dev` proxies both
 * there, as nginx does in the image. The session cookie then belongs to the
 * dev server's own origin, as it belongs to nginx's.
 */

import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

const env = process.env;

/** The auth service: the compose default, overridable for a local process. */
const target = env.NL2SQL_AUTH_URL ?? "https://localhost:8446";

/**
 * Whether to check its certificate. False by default because the default is
 * the development certificate the agent API wrote for itself, on a
 * developer's own machine; in the image the equivalent is on.
 */
const secure = (env.NL2SQL_AUTH_TLS_VERIFY ?? "false").toLowerCase() === "true";

const proxy: ProxyOptions = { target, changeOrigin: true, secure };

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(env.DIRECTORY_GUI_PORT ?? 5177),
    proxy: { "/directory": proxy, "/auth": proxy },
  },
  preview: {
    port: Number(env.DIRECTORY_GUI_PORT ?? 5177),
    proxy: { "/directory": proxy, "/auth": proxy },
  },
  build: { outDir: "dist", sourcemap: true },
});
