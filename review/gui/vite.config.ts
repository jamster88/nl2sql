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
import { defineConfig, type ProxyOptions } from "vite";

/** Paths the review service owns. Everything else is this app's own asset. */
const API_PATHS = ["/v1", "/healthz", "/readyz", "/openapi.json"];

const env = process.env;
const target = env.NL2SQL_REVIEW_URL ?? "https://localhost:8444";
const secure = (env.NL2SQL_REVIEW_TLS_VERIFY ?? "false").toLowerCase() === "true";
const token = env.REVIEW_TOKEN ?? "";

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
    port: Number(env.REVIEW_GUI_PORT ?? 5174),
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, proxy])),
  },
  preview: {
    port: Number(env.REVIEW_GUI_PORT ?? 5174),
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, proxy])),
  },
  build: { outDir: "dist", sourcemap: true },
});
