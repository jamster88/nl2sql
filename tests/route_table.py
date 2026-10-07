"""An application's routes, as a test walks them.

Since FastAPI 0.141 a router included in an application stays one object
in `app.routes` (`_IncludedRouter`) rather than being copied into it route
by route; its routes, each already carrying the router's dependencies, are
on the router it wraps. The walks over a route table -- every route guarded,
every route refusing a stranger -- need them all.
"""

from __future__ import annotations

from typing import Iterable, Iterator


def flattened(routes: Iterable) -> Iterator:
    """Every route, from inside every router included, however deep."""
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from flattened(original.routes)
        else:
            yield route
