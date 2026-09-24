"""`python -m nl2sql_review` -- serve the review API.

Its own entrypoint rather than a mode of the agent's, because this process
carries different powers and a different token. `raise SystemExit` rather
than `sys.exit` so the module is runnable through `runpy` and the exit code
is still the server's.
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
