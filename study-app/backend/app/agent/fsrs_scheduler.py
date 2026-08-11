"""FSRS (Free Spaced Repetition Scheduler) integration layer.

Wraps the `fsrs` library so the rest of the codebase deals with plain dicts.
FSRS state is stored per-concept inside the concept_mastery JSON entries.

The FSRS algorithm schedules reviews at scientifically-optimal intervals to
maximize long-term retention. Each concept has a stability (memory strength),
difficulty, and due date. When a learner answers a quiz question or reviews a
flashcard, the concept's FSRS state is updated and the next review is scheduled.

Rating mapping (from our 2-button UI + quiz correctness):
  - "I know this" / quiz correct → Rating.Good (3)
  - "Still learning" / quiz wrong → Rating.Again (1)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fsrs import Card, Rating, Scheduler

# Module-level scheduler singleton. The default parameters are well-tuned
# (derived from millions of reviews in Anki's dataset).
_scheduler = Scheduler()


def schedule_review(
    fsrs_state: dict[str, Any] | None, rating: int
) -> dict[str, Any]:
    """Review a concept and return its updated FSRS state.

    Args:
        fsrs_state: The existing FSRS state dict (from a prior review), or
                    None if this is the concept's first review.
        rating: FSRS rating — 1=Again, 2=Hard, 3=Good, 4=Easy.

    Returns:
        A plain dict with {stability, difficulty, due, last_review, state, step}
        suitable for JSON storage inside concept_mastery.
    """
    # Reconstruct the Card from prior state, or create a new one.
    if fsrs_state:
        # Rebuild the Card from its JSON representation.
        card_json = json.dumps(_state_to_card_json(fsrs_state))
        card = Card.from_json(card_json)
    else:
        card = Card()

    # Run the scheduler.
    updated_card, _log = _scheduler.review_card(card, Rating(rating))

    # Extract a storage-friendly dict.
    return {
        "stability": updated_card.stability,
        "difficulty": updated_card.difficulty,
        "due": updated_card.due.isoformat() if updated_card.due else None,
        "last_review": (
            updated_card.last_review.isoformat()
            if updated_card.last_review
            else None
        ),
        "state": int(updated_card.state),
        "step": getattr(updated_card, "step", None),
    }


def _state_to_card_json(state: dict[str, Any]) -> dict[str, Any]:
    """Convert our stored state dict back into the JSON format fsrs.Card expects."""
    # Reconstruct the card_id as a timestamp-based int (FSRS uses epoch ms).
    # The actual value doesn't matter for scheduling — only stability,
    # difficulty, due, last_review, state, step drive the algorithm.
    due_str = state.get("due")
    last_str = state.get("last_review")

    def _to_dt(s: str | None) -> str | None:
        if s is None:
            return None
        return s  # FSRS from_json accepts ISO strings

    return {
        "card_id": int(datetime.now(timezone.utc).timestamp() * 1000),
        "state": state.get("state", 1),
        "step": state.get("step", 0),
        "stability": state.get("stability"),
        "difficulty": state.get("difficulty"),
        "due": _to_dt(due_str),
        "last_review": _to_dt(last_str),
    }


def is_due(fsrs_state: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """True if the concept is due for review now.

    A concept is due if:
      - It has no FSRS state (new concept — due immediately), OR
      - Its due date has passed.
    """
    if not fsrs_state:
        return True  # new concepts are due immediately

    due_str = fsrs_state.get("due")
    if not due_str:
        return True

    now = now or datetime.now(timezone.utc)
    try:
        due_dt = datetime.fromisoformat(due_str)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        return now >= due_dt
    except (ValueError, TypeError):
        return True


def due_in_days(
    fsrs_state: dict[str, Any] | None, now: datetime | None = None
) -> float | None:
    """Days until the concept is due (negative = overdue). None if new/untested."""
    if not fsrs_state:
        return None

    due_str = fsrs_state.get("due")
    if not due_str:
        return None

    now = now or datetime.now(timezone.utc)
    try:
        due_dt = datetime.fromisoformat(due_str)
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        delta = (due_dt - now).total_seconds()
        return round(delta / 86400, 2)  # days
    except (ValueError, TypeError):
        return None
