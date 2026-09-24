/**
 * Build and dev-server configuration.
 *
 * The interesting part is the proxy. A browser talking straight to the API
 * has two problems that have nothing to do with this application: the
 * development certificate is self-signed, so every request is blocked until
 * someone clicks through a warning -- and `EventSource` gives no warning to
 * click, it simply never connects -- and the API token would have to be
 * shipped to the browser to be sent from it.
 *
 * So `npm run dev` puts a proxy in front, exactly as the production image
 * puts nginx in front (see `nginx.conf`, which is the same three rules in
 * another syntax). The browser then speaks same-origin HTTP to something on
 * localhost, and the certificate and the token stay on the server side of
 * that hop. This is the "intermediary service" the API documentation
 * recommends; it is worth knowing that it is a convenience rather than a
 * requirement, because the API also answers a browser directly when
 * `API_CORS_ORIGINS` names it.
 */

import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

/** Paths the API owns. Everything else is this application's own asset. */
const API_PATHS = ["/v1", "/healthz", "/readyz", "/openapi.json"];

const env = process.env;

/** Where the API is. The compose default, overridable for a local process. */
const target = env.NL2SQL_API_URL ?? "https://localhost:8443";

/**
 * Whether to check the API's certificate. False by default because the
 * default certificate is the one the server wrote for itself, and this hop
 * is a developer's own machine. In the production image the equivalent
 * setting is nginx's `proxy_ssl_verify`, which is *on*, because there the
 * hop crosses a container network.
 */
const secure = (env.NL2SQL_API_TLS_VERIFY ?? "false").toLowerCase() === "true";

const token = env.API_TOKEN ?? "";

const proxy: ProxyOptions = {
  target,
  changeOrigin: true,
  secure,
  // Server-Sent Events are a response that never ends. Both of these stop
  // the proxy from waiting for one to finish before passing anything on.
  ws: false,
  timeout: 0,
  proxyTimeout: 0,
  configure(server) {
    server.on("proxyReq", (request) => {
      if (token) request.setHeader("Authorization", `Bearer ${token}`);
      // Asking for an identity encoding keeps a compressing proxy from
      // buffering the event stream to find something worth compressing.
      request.setHeader("Accept-Encoding", "identity");
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(env.GUI_PORT ?? 5173),
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, proxy])),
  },
  preview: {
    port: Number(env.GUI_PORT ?? 5173),
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, proxy])),
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
