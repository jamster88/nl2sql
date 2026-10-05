/**
 * The development server's proxies, which every page's `vite.config.ts`
 * builds the same way (V6-25).
 *
 * `npm run dev` puts a proxy in front of a page's service exactly as the
 * image puts nginx in front of it: the browser speaks same-origin HTTP to
 * localhost, and the service's development certificate and any token stay
 * on the server side of that hop. Sign-in (`/auth`) goes to the auth
 * service, whose cookie then comes back as the dev server's own, as it is
 * nginx's in the image.
 */

/** The part of Vite's `ProxyOptions` this uses, so the shared code needs no Vite types. */
export interface Proxy {
  target: string;
  changeOrigin: boolean;
  secure: boolean;
  ws?: boolean;
  timeout?: number;
  proxyTimeout?: number;
  configure?: (server: { on(event: "proxyReq", listener: (request: ProxyRequest) => void): unknown }) => void;
}

/** The outgoing request, as a proxy listener sees it. */
export interface ProxyRequest {
  setHeader(name: string, value: string): void;
}

export interface DevProxyOptions {
  /** The paths the page's service owns. */
  paths: string[];
  /** Where that service is. */
  target: string;
  /** Where the auth service is; no `/auth` proxy without one. */
  auth?: string;
  /** Whether to check the services' certificates. */
  secure: boolean;
  /** A token to send as the bearer, for a service with sign-in off. */
  token?: string;
  /**
   * Whether the service streams (Server-Sent Events): then neither the
   * proxy nor anything compressing behind it may wait for a response to end.
   */
  stream?: boolean;
}

/** The `server.proxy` (and `preview.proxy`) object for one page. */
export function devProxies(options: DevProxyOptions): Record<string, Proxy> {
  const { paths, target, auth, secure, token = "", stream = false } = options;
  const service: Proxy = {
    target,
    changeOrigin: true,
    secure,
    ...(stream ? { ws: false, timeout: 0, proxyTimeout: 0 } : {}),
    configure(server) {
      server.on("proxyReq", (request) => {
        if (token) request.setHeader("Authorization", `Bearer ${token}`);
        // Asking for an identity encoding keeps a compressing proxy from
        // buffering the event stream to find something worth compressing.
        if (stream) request.setHeader("Accept-Encoding", "identity");
      });
    },
  };
  const proxies: Record<string, Proxy> = Object.fromEntries(paths.map((path) => [path, service]));
  if (auth) proxies["/auth"] = { target: auth, changeOrigin: true, secure };
  return proxies;
}
