# The review GUI: built with node, served by nginx, with no node in the result.
#
# A separate image from the web GUI's, built from a separate npm project, and
# that separation is the point rather than an accident of layout. Two Vite
# entry points in one project would share a build, and the public GUI's image
# would then be serving the interface that rewrites the golden question set
# to anyone who could reach it. A second package.json is a few more files and
# a boundary that cannot be crossed by forgetting something.

FROM --platform=$BUILDPLATFORM node:26-alpine AS build

WORKDIR /build

# The lockfile alone first, so the dependency layer is only rebuilt when the
# dependencies actually change rather than on every source edit.
COPY review/gui/package.json review/gui/package-lock.json ./
RUN npm ci

COPY review/gui/tsconfig.json review/gui/vite.config.ts review/gui/index.html ./
COPY review/gui/src/ ./src/
# `npm run build` is `tsc --noEmit && vite build`, so an image cannot be
# produced from code that does not compile.
RUN npm run build


FROM nginx:1.29-alpine

# `default.conf` ships with the image and would otherwise win on port 80.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /build/dist/ /usr/share/nginx/html/
COPY review/gui/nginx.conf.template /etc/nginx/templates/nl2sql-review.conf.template
COPY review/gui/10-nl2sql-review-config.envsh /docker-entrypoint.d/10-nl2sql-review-config.envsh
RUN chmod +x /docker-entrypoint.d/10-nl2sql-review-config.envsh

# Substituted into the template at start-up. Only these names are touched --
# without the filter, envsubst would also eat nginx's own $uri and $host.
ENV NGINX_ENVSUBST_FILTER="^REVIEW_" \
    REVIEW_GUI_PORT=8081 \
    REVIEW_UPSTREAM=https://nl2sql-review:8444 \
    REVIEW_CACERT=/etc/nl2sql/tls/server.crt \
    REVIEW_SSL_NAME=nl2sql-review \
    REVIEW_READ_TIMEOUT=900s \
    REVIEW_GUI_RESOLVER=127.0.0.11

# REVIEW_TOKEN is deliberately not given a default. A token baked into an
# image is a token in every layer of it -- and this one can rewrite the
# golden question set. An unset one is what the entrypoint script expects:
# it turns "no token" into an empty Authorization header, which nginx then
# does not send at all.

# Where the agent API's certificate is expected to appear, mounted by compose
# from the volume the API writes it into.
RUN mkdir -p /etc/nl2sql/tls

EXPOSE 8081

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${REVIEW_GUI_PORT}/index.html" || exit 1

LABEL org.opencontainers.image.title="nl2sql feedback review GUI" \
      org.opencontainers.image.description="React/TypeScript interface for reviewing feedback and promoting it into the golden question set" \
      org.opencontainers.image.version="4.5.0"
