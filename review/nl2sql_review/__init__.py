"""Reviewing captured feedback, and promoting it into the golden question set.

These names are what a host application, a test or a different ASGI server
is expected to reach for. Everything else is an implementation detail of one
of them.

`promote` is deliberately **not** re-exported here even though it is the
most important function in the package. It would shadow `promote.py`, so
`from nl2sql_review import promote` would hand back the function to one
caller and the module to another depending only on whether the package had
finished importing -- which is the kind of bug that is found by a test
failing for a reason that has nothing to do with the test. It is reached as
`nl2sql_review.promote.promote`.
"""

from .app import __version__, create_app
from .promote import Promotion, PromotionError
from .render import Draft
from .settings import ReviewSettings
from .store import Repository, Submission

__all__ = [
    "Draft",
    "Promotion",
    "PromotionError",
    "Repository",
    "ReviewSettings",
    "Submission",
    "__version__",
    "create_app",
]
