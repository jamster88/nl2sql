# The SQL console's interface: built with node, served by nginx, with no node
# in the result.
#
# A separate image from the other two interfaces', built from a separate npm
# project, for the reason the review interface is separate: an entry point
# in the public GUI's project would be served by the public GUI's image, to
# anyone who could reach it. This page runs SQL; it is opt-in, and it is
# its own image so that opting out means it is not there at all.
#
# The console itself is not in here. It runs from the agent's image
# (`python -m nl2sql_agent.console`), because every answer it gives has to be
# the agent's own; this is only the page and the proxy in front of it.

FROM --platform=$BUILDPLATFORM node:26-alpine AS build

WORKDIR /build

# The lockfile alone first, so the dependency layer is only rebuilt when the
# dependencies actually change rather than on every source edit.
COPY console/package.json console/package-lock.json ./
RUN npm ci

COPY console/tsconfig.json console/vite.config.ts console/index.html ./
COPY console/src/ ./src/
# `npm run build` is `tsc --noEmit && vite build`, so an image cannot be
# produced from code that does not compile.
RUN npm run build


FROM nginx:1.29-alpine

# `default.conf` ships with the image and would otherwise win on port 80.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /build/dist/ /usr/share/nginx/html/
COPY console/nginx.conf.template /etc/nginx/templates/nl2sql-console.conf.template
COPY console/10-nl2sql-console-config.envsh /docker-entrypoint.d/10-nl2sql-console-config.envsh
RUN chmod +x /docker-entrypoint.d/10-nl2sql-console-config.envsh

# Substituted into the template at start-up. Only these names are touched --
# without the filter, envsubst would also eat nginx's own $uri and $host.
ENV NGINX_ENVSUBST_FILTER="^(CONSOLE_|AUTH_)" \
    CONSOLE_GUI_TLS_ENABLED=true \
    CONSOLE_GUI_TLS_CERT_FILE=/etc/nl2sql/tls/server.crt \
    CONSOLE_GUI_TLS_KEY_FILE=/etc/nl2sql/tls/server.key \
    AUTH_UPSTREAM=https://nl2sql-auth:8446 \
    AUTH_CACERT=/etc/nl2sql/tls/ca.crt \
    AUTH_SSL_NAME=nl2sql-auth \
    CONSOLE_GUI_PORT=8082 \
    CONSOLE_UPSTREAM=https://nl2sql-console:8445 \
    CONSOLE_CACERT=/etc/nl2sql/tls/ca.crt \
    CONSOLE_SSL_NAME=nl2sql-console \
    CONSOLE_READ_TIMEOUT=120s \
    CONSOLE_GUI_RESOLVER=127.0.0.11

# CONSOLE_TOKEN is deliberately not given a default. A token baked into an
# image is a token in every layer of it. An unset one is what the entrypoint
# script expects: it turns "no token" into an empty Authorization header,
# which nginx then does not send at all.

# Where this page's own certificate and the stack's CA are expected to
# appear, mounted by compose from the volume the pki service issues them into.
RUN mkdir -p /etc/nl2sql/tls

EXPOSE 8082

# HTTPS unless the page was told otherwise. Unverified, and only here: this is
# the container checking its own socket, where the certificate's name proves
# nothing.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD case "$CONSOLE_GUI_TLS_ENABLED" in false|0|no|off) s=http ;; *) s=https ;; esac; \
        wget -q --no-check-certificate -O /dev/null "$s://127.0.0.1:${CONSOLE_GUI_PORT}/index.html" || exit 1

LABEL org.opencontainers.image.title="nl2sql SQL console GUI" \
      org.opencontainers.image.description="React/TypeScript interface for querying the retail database through the agent's gates" \
      org.opencontainers.image.version="6.1.0"
