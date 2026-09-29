"""The SQL console: the retail database, as the agent sees it.

A troubleshooting tool, and a process of its own. When the agent answers a
question wrongly, the questions worth asking next are about the database it
read and the gates in front of it -- would the validator have passed this
query, what did the planner estimate, did it finish inside the timeout, what
did the rows actually say -- and every one of them has an exact answer that
only running SQL can give.

It runs from the agent's image, as the API does, because the answers have to
be the agent's: the same role, the same limits, the same validator and the
same introspection, imported rather than re-implemented. It is a separate
process from the API because of what it does. The API runs SQL the pipeline
wrote; this runs SQL a person typed, and whoever can reach the one should not
thereby be able to do the other.
"""

from .app import create_app
from .settings import ConsoleSettings

__all__ = ["ConsoleSettings", "create_app"]
