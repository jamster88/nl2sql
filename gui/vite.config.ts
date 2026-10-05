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
import { defineConfig } from "vite";

import { sharedPackage } from "../web/src/config.ts";
import { devProxies } from "../web/src/vite.ts";

const shared = sharedPackage(import.meta.url);

/** Paths the API owns. Everything else is this application's own asset. */
const API_PATHS = ["/v1", "/healthz", "/readyz", "/openapi.json"];

const env = process.env;

/**
 * The API, the compose default or a local process -- and its certificate is
 * not checked by default, because the default certificate is the one the
 * stack's development CA issued and this hop is a developer's own machine.
 * In the production image the equivalent setting is nginx's
 * `proxy_ssl_verify`, which is *on*, because there the hop crosses a
 * container network. API_TOKEN is sent as the bearer, and nothing holds back
 * the event stream. Sign-in goes to the auth service.
 */
const proxies = devProxies({
  paths: API_PATHS,
  target: env.NL2SQL_API_URL ?? "https://localhost:8443",
  auth: env.NL2SQL_AUTH_URL ?? "https://localhost:8446",
  secure: (env.NL2SQL_API_TLS_VERIFY ?? "false").toLowerCase() === "true",
  token: env.API_TOKEN ?? "",
  stream: true,
});

export default defineConfig({
  plugins: [react()],
  resolve: shared.resolve,
  server: {
    fs: shared.server.fs,
    port: Number(env.GUI_PORT ?? 5173),
    proxy: proxies,
  },
  preview: {
    port: Number(env.GUI_PORT ?? 5173),
    proxy: proxies,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
