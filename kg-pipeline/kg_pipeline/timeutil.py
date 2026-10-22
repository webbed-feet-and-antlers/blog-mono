"""Time normalization: everything converges on ISO 8601 + a granularity tag.

Accepted inputs: "YYYY", "YYYY-MM", "YYYY-MM-DD", "YYYY-MM-DDTHH:...", or an
int year. Anything else raises ValueError. Granularity is always derived from
the value itself, never trusted from metadata.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional, Tuple

_YEAR_RE = re.compile(r"^\d{4}$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Coarser granularity wins when a from/to pair disagrees, so intervals never
# claim more precision than their weakest bound.
_GRAN_RANK = {"year": 0, "month": 1, "day": 2}


def normalize_time(value) -> Tuple[Optional[str], Optional[str]]:
    """Return (iso, granularity), or (None, None) for empty input."""
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, int):
        value = str(value)
    s = str(value).strip()
    if not s:
        return None, None

    day = _DAY_RE.match(s[:10]) if len(s) >= 10 else None
    if day:
        date(int(day.group(1)), int(day.group(2)), int(day.group(3)))  # calendar check
        return s[:10], "day"
    month = _MONTH_RE.match(s)
    if month and 1 <= int(month.group(2)) <= 12:
        return s, "month"
    if _YEAR_RE.match(s):
        return s, "year"
    raise ValueError(f"unrecognized date value {value!r}")


def granularity_of(iso: str) -> str:
    _, g = normalize_time(iso)
    if g is None:
        raise ValueError(f"not an ISO date: {iso!r}")
    return g


def coarser_granularity(*isos: str) -> str:
    gs = [granularity_of(i) for i in isos if i]
    return min(gs, key=lambda g: _GRAN_RANK[g]) if gs else "day"


def valid_iso(iso: Optional[str]) -> bool:
    if iso is None:
        return False
    try:
        normalized, g = normalize_time(iso)
    except ValueError:
        return False
    return normalized == iso and g is not None


def time_appears_in_text(iso: str, text: str) -> bool:
    """Best-effort check whether a date's value is stated in the text.

    A day-granularity bound counts as explicit if the full date, the year-month,
    or the year (as a standalone number) appears; year matching uses digit
    boundaries so "2009" does not match inside "120091".
    """
    g = granularity_of(iso)
    candidates = []
    if g == "day":
        candidates = [iso, iso[:7], iso[:4]]
    elif g == "month":
        candidates = [iso, iso[:4]]
    else:
        candidates = [iso]
    for c in candidates:
        if len(c) == 4:
            if re.search(rf"(?<!\d){re.escape(c)}(?!\d)", text):
                return True
        elif c in text:
            return True
    return False
