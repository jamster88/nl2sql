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
import { defineConfig, type ProxyOptions } from "vite";

/** Paths the console owns. Everything else is this app's own asset. */
const API_PATHS = ["/v1", "/healthz", "/readyz", "/openapi.json"];

const env = process.env;
const target = env.NL2SQL_CONSOLE_URL ?? "https://localhost:8445";
const secure = (env.NL2SQL_CONSOLE_TLS_VERIFY ?? "false").toLowerCase() === "true";
const token = env.CONSOLE_TOKEN ?? "";

const proxy: ProxyOptions = {
  target,
  changeOrigin: true,
  secure,
  configure(server) {
    server.on("proxyReq", (request) => {
      if (token) request.setHeader("Authorization", `Bearer ${token}`);
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(env.CONSOLE_GUI_PORT ?? 5175),
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, proxy])),
  },
  preview: {
    port: Number(env.CONSOLE_GUI_PORT ?? 5175),
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, proxy])),
  },
  build: { outDir: "dist", sourcemap: true },
});
