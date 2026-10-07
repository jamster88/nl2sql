"""Database values, as JSON can carry them."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal
from typing import Any


def json_safe(value: Any) -> Any:
    """A cell as JSON can carry it without losing what it said.

    Decimals become strings rather than floats: `719279.97` is the answer
    being checked, and a float would show `719279.9699999999`. A float that
    is not finite is a string too, because JSON has no spelling for NaN and
    the response would otherwise fail to serialise at all.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, clock)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, timedelta):
        return str(value)
    return str(value)
