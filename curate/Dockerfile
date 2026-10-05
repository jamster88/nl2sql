# The curation GUI: built with node, served by nginx, with no node in the result.
#
# A fourth npm project and image, for the reason the review and console
# interfaces are their own: an entry point in another project would be served
# by that project's image, and this page writes what the agent is shown --
# SQL snippets -- and what it is measured against -- the golden set. Its
# backend is the review service, the one process with those powers.

FROM --platform=$BUILDPLATFORM node:26-alpine AS build

WORKDIR /build

# The lockfile alone first, so the dependency layer is only rebuilt when the
# dependencies actually change rather than on every source edit.
COPY curate/package.json curate/package-lock.json ./
RUN npm ci

COPY curate/tsconfig.json curate/vite.config.ts curate/index.html ./
COPY curate/src/ ./src/
# What every page shares (V6-25), where its configuration looks for it:
# beside the page in the checkout, so `../web` (or `../../web`) from /build.
COPY web/package.json /web/package.json
COPY web/src/ /web/src/
# `npm run build` is `tsc --noEmit && vite build`, so an image cannot be
# produced from code that does not compile.
RUN npm run build


FROM nginx:1.29-alpine

# `default.conf` ships with the image and would otherwise win on port 80.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /build/dist/ /usr/share/nginx/html/
COPY curate/nginx.conf.template /etc/nginx/templates/nl2sql-curate.conf.template
COPY curate/10-nl2sql-curate-config.envsh /docker-entrypoint.d/10-nl2sql-curate-config.envsh
RUN chmod +x /docker-entrypoint.d/10-nl2sql-curate-config.envsh

# Substituted into the template at start-up. Only these names are touched --
# without the filter, envsubst would also eat nginx's own $uri and $host.
ENV NGINX_ENVSUBST_FILTER="^(CURATE_|AUTH_)" \
    CURATE_GUI_TLS_ENABLED=true \
    CURATE_GUI_TLS_CERT_FILE=/etc/nl2sql/tls/server.crt \
    CURATE_GUI_TLS_KEY_FILE=/etc/nl2sql/tls/server.key \
    AUTH_UPSTREAM=https://nl2sql-auth:8446 \
    AUTH_CACERT=/etc/nl2sql/tls/ca.crt \
    AUTH_SSL_NAME=nl2sql-auth \
    CURATE_GUI_PORT=8083 \
    CURATE_UPSTREAM=https://nl2sql-review:8444 \
    CURATE_CACERT=/etc/nl2sql/tls/ca.crt \
    CURATE_SSL_NAME=nl2sql-review \
    CURATE_READ_TIMEOUT=900s \
    CURATE_GUI_RESOLVER=127.0.0.11

# CURATE_TOKEN is deliberately not given a default. A token baked into an
# image is a token in every layer of it -- and this one is the review
# service's, which can rewrite the golden set and the snippets. Unset is what
# the entrypoint script expects: it turns "no token" into an empty
# Authorization header, which nginx then does not send at all.

# Where this page's own certificate and the stack's CA are expected to
# appear, mounted by compose from the volume the pki service issues them into.
RUN mkdir -p /etc/nl2sql/tls

# Unprivileged (V6-31): nginx's own account, uid 101, which the pki service
# gives this page's key to. What the start writes -- the templates rendered
# into conf.d, the include files beside nginx.conf, the pid, the cache -- is
# made that account's here, and the `user` directive goes: a master that is
# not root cannot switch to it.
RUN sed -i -e '/^user /d' -e 's#^pid .*#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
 && chown nginx:nginx /etc/nginx /etc/nginx/conf.d /var/cache/nginx
USER 101:101

EXPOSE 8083

# HTTPS unless the page was told otherwise. Unverified, and only here: this is
# the container checking its own socket, where the certificate's name proves
# nothing.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD case "$CURATE_GUI_TLS_ENABLED" in false|0|no|off) s=http ;; *) s=https ;; esac; \
        wget -q --no-check-certificate -O /dev/null "$s://127.0.0.1:${CURATE_GUI_PORT}/index.html" || exit 1

LABEL org.opencontainers.image.title="nl2sql curation GUI" \
      org.opencontainers.image.description="React/TypeScript interface for curating SQL snippets, golden pairs, corrections and completions" \
      org.opencontainers.image.version="6.2.0"
