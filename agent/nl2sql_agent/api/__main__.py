"""`python -m nl2sql_agent.api` -- the REST server.

Deliberately the same package as the CLI: one image, two ways in. The
container's default entrypoint is still `python -m nl2sql_agent`, and the
compose `api` service overrides the command with this, so the thing serving
a GUI is exactly the thing that was benchmarked.
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
