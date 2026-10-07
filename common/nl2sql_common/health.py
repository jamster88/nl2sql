"""A service's health check that verifies what it serves (V6-37).

`python -m nl2sql_common.health API` -- or REVIEW, CONSOLE, AUTH: the prefix
of the service's own settings. Until 6.3 every check asked the service's
port with `ssl._create_unverified_context()`, which passes for a service
presenting an expired certificate, or another service's. This asks
`/healthz` over the service's own listener under the name `localhost`,
which every certificate the pki service issues covers, and verifies the
answer against:

* `<PREFIX>_TLS_CA_FILE`, when set;
* else the CA's certificate beside the service's own (`ca.crt`, where the
  pki service puts it);
* else the service's certificate itself -- one the service generated for
  itself, which is its own issuer.

Plain HTTP when `<PREFIX>_TLS_ENABLED` says so: there is nothing to verify.
Exits 0 when the service answered, 1 with the reason when it did not.
"""

from __future__ import annotations

import ssl
import sys
import urllib.request
from pathlib import Path

from .env import env, env_bool, env_str

#: Each service's port when its settings name none.
PORTS = {"API": 8443, "REVIEW": 8444, "CONSOLE": 8445, "AUTH": 8446}


def target(prefix: str) -> tuple[str, ssl.SSLContext | None]:
    """The URL to ask, and the context that verifies it (None for HTTP)."""
    port = env_str(f"{prefix}_PORT", str(PORTS.get(prefix, 8443)))
    if not env_bool(f"{prefix}_TLS_ENABLED", True):
        return f"http://127.0.0.1:{port}/healthz", None
    cert = Path(env_str(f"{prefix}_TLS_CERT_FILE", "/etc/nl2sql/tls/server.crt"))
    beside = cert.with_name("ca.crt")
    ca = env(f"{prefix}_TLS_CA_FILE") or str(beside if beside.exists() else cert)
    return f"https://localhost:{port}/healthz", ssl.create_default_context(cafile=ca)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m nl2sql_common.health PREFIX   (API, REVIEW, CONSOLE or AUTH)", file=sys.stderr)
        return 2
    url, context = target(args[0].upper())
    try:
        with urllib.request.urlopen(url, context=context, timeout=5) as answer:
            answer.read()
    except (OSError, ssl.SSLError) as exc:
        print(f"unhealthy: {url}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
