# An outside client for the REST API.
#
# Deliberately not built from the agent image and deliberately not Python:
# the point of this container is to prove that something with no knowledge of
# this project -- no shared code, no shared dependencies, only HTTP and TLS --
# can ask a question and read the answer. If the smoke test had to import
# the agent package, it would not be testing an API.
#
# curl and jq are all it has, which is also the smallest thing a GUI author
# can copy from: every request in smoke.sh translates line for line into
# fetch(), requests, or HttpClient.
FROM alpine:3.21

RUN apk add --no-cache curl jq ca-certificates bash

COPY docker/apitest/smoke.sh /usr/local/bin/smoke.sh
RUN chmod +x /usr/local/bin/smoke.sh

# Where the server's certificate is expected to appear -- mounted by compose
# from the volume the API writes it into. The directory exists in the image so
# the certificate can also be placed here by `docker cp` on a stopped
# container, which is how the test suite trusts it without a bind mount.
RUN mkdir -p /etc/nl2sql/tls

LABEL org.opencontainers.image.title="nl2sql API smoke test" \
      org.opencontainers.image.description="curl-only external client for the nl2sql agent REST API"

ENTRYPOINT ["/usr/local/bin/smoke.sh"]
