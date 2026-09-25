# The GUI: built with node, served by nginx, with no node in the result.
#
# Two stages because the two jobs want different things. The build needs a
# toolchain, the whole dependency tree and the TypeScript compiler; what it
# produces is a few hundred kilobytes of static files that need none of it.
# Shipping the builder would mean shipping npm and its dependency tree into
# production for no reason.
#
# The type check runs here as well as in the test suite (`npm run build` is
# `tsc --noEmit && vite build`), so an image cannot be produced from code
# that does not compile.

# Pinned to the *build* platform, not the target. What this stage produces is
# JavaScript, CSS and HTML -- identical whatever the image will run on -- so
# building it once natively is both correct and the difference between a
# multi-arch publish that takes a minute and one that runs `npm ci` under
# QEMU emulation for every architecture.
FROM --platform=$BUILDPLATFORM node:26-alpine AS build

WORKDIR /build

# The lockfile alone first, so the dependency layer is only rebuilt when the
# dependencies actually change rather than on every source edit.
COPY gui/package.json gui/package-lock.json ./
RUN npm ci

COPY gui/tsconfig.json gui/vite.config.ts gui/index.html ./
COPY gui/src/ ./src/
RUN npm run build


FROM nginx:1.29-alpine

# `default.conf` ships with the image and would otherwise win on port 80.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /build/dist/ /usr/share/nginx/html/
COPY gui/nginx.conf.template /etc/nginx/templates/nl2sql.conf.template
COPY gui/10-nl2sql-config.envsh /docker-entrypoint.d/10-nl2sql-config.envsh
RUN chmod +x /docker-entrypoint.d/10-nl2sql-config.envsh

# Substituted into the template at start-up. Only these names are touched --
# without the filter, envsubst would also eat nginx's own $uri and $host.
ENV NGINX_ENVSUBST_FILTER="^(API_|GUI_)" \
    GUI_PORT=8080 \
    API_UPSTREAM=https://nl2sql-api:8443 \
    API_CACERT=/etc/nl2sql/tls/server.crt \
    API_SSL_NAME=nl2sql-api \
    API_READ_TIMEOUT=600s \
    GUI_RESOLVER=127.0.0.11

# API_TOKEN is deliberately not given a default here. A token baked into an
# image is a token in every layer of it, and an unset one is what the
# entrypoint script already expects -- it turns "no token" into an empty
# Authorization header, which nginx then does not send at all.

# Where the API's certificate is expected to appear, mounted by compose from
# the volume the API writes it into. The directory exists in the image so the
# certificate can also be placed here by `docker cp`.
RUN mkdir -p /etc/nl2sql/tls

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${GUI_PORT}/index.html" || exit 1

LABEL org.opencontainers.image.title="nl2sql GUI" \
      org.opencontainers.image.description="React/TypeScript web interface for the nl2sql agent REST API" \
      org.opencontainers.image.version="4.4.0"
