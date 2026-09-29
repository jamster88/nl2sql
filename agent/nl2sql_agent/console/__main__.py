"""`python -m nl2sql_agent.console` -- the SQL console's server.

The same image as the agent and the API, a third way in. The compose
`console` service overrides the command with this.
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
