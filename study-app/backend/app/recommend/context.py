"""UserContext — the unified context object all strategies evaluate against.

Composed from every available signal: FSRS due concepts, mastery, learner
profile, content coverage, session history, and (future) calendar/exams.
Strategies never query the DB directly — they read from this pre-computed
context, which is built once per /api/recommend call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import behavior as behavior_store
from ..agent import memory as memory_store
from ..agent import fsrs_scheduler
from ..agent.reflection import get_learner_insights
from ..models import ContentItem, Document


@dataclass
class SessionAction:
    """A single action the user took in the current session."""
    tool: str          # "quiz" | "flashcards" | "notes" | "generate"
    doc_id: str | None
    ts: str            # ISO timestamp


@dataclass
class SessionContext:
    """The user's current study session — recent actions + fatigue level."""
    actions: list[SessionAction] = field(default_factory=list)
    started_at: str | None = None
    duration_mins: float = 0.0
    fatigue_level: str = "fresh"   # "fresh" (<20m) | "focused" (20-50m) | "fatigued" (50m+)
    dismissed_tools: set[str] = field(default_factory=set)


@dataclass
class RecommendationResult:
    """A single strategy's evaluation output."""
    strategy_name: str
    category: str             # "organize" | "learn" | "practice" | "onboarding"
    action: str               # the action string the frontend consumes
    title: str
    rationale: str
    score: float              # 0.0 to 1.0
    document_id: str | None = None
    tab: str | None = None
    ready: bool = False
    deck: dict | None = None
    content_id: str | None = None
    dismissible: bool = True

    def to_dict(self) -> dict:
        """Convert to the API response format (backward-compatible)."""
        return {
            "action": self.action,
            "title": self.title,
            "rationale": self.rationale,
            "document_id": self.document_id,
            "tab": self.tab,
            "ready": self.ready,
            "deck": self.deck,
            "content_id": self.content_id,
            "strategy_name": self.strategy_name,
            "dismissible": self.dismissible,
            "score": round(self.score, 3),
        }


@dataclass
class UserContext:
    """All signals composed into one context object for strategy evaluation."""
    # FSRS / mastery
    due_concepts: list[dict] = field(default_factory=list)
    due_cards: list[dict] = field(default_factory=list)
    concept_mastery: dict = field(default_factory=dict)
    weak_topics: list[dict] = field(default_factory=list)
    # Learner profile
    profile: dict = field(default_factory=dict)
    # Content coverage
    documents: dict[str, Document] = field(default_factory=dict)
    content_by_doc: dict[str, dict[str, list]] = field(default_factory=dict)
    analyses: dict[str, dict] = field(default_factory=dict)
    proactive_decks: list = field(default_factory=list)
    # Session
    session: SessionContext | None = None
    # Behavioral understanding (the same keys reflection/generation consume).
    # Design rule: scoring reads the measurements, never the LLM's prose.
    insights: dict = field(default_factory=dict)
    patterns: dict = field(default_factory=dict)
    engagement: dict = field(default_factory=dict)
    slow_concepts: list[dict] = field(default_factory=list)
    neglected_docs: list[str] = field(default_factory=list)
    is_peak_hour: bool = False
    # Feature flags
    enabled_features: set[str] = field(default_factory=lambda: {
        "onboarding", "due_review_ready", "due_review_generate",
        "weak_spot", "proactive_deck", "quiz_gap", "start_notes",
        "quiz", "flashcards", "fallback",
    })
    # Learned weights (from bandit, Phase 4)
    weights: dict[str, list[float]] = field(default_factory=dict)
    # Computed helpers
    due_count: int = 0
    mastered_count: int = 0
    total_concepts: int = 0
    welcome_back: str | None = None


async def build_context(session: AsyncSession) -> UserContext:
    """Compose all signals into a UserContext. Called once per /api/recommend."""
    # FSRS / mastery signals
    due_concepts = await memory_store.get_due_concepts(session)
    mastery = await memory_store.get_concept_mastery(session)
    weak_topics = await memory_store.get_weak_topics(session) or []
    profile = await memory_store.get_learner_profile(session)

    # Documents + analyses
    doc_rows = await session.execute(
        select(Document).order_by(Document.uploaded_at.desc())
    )
    documents = {d.id: d for d in doc_rows.scalars().all()}

    analyses_raw = await memory_store.list_memory(session, scope="doc")
    analyses = {
        m.ref_id: m.value or {}
        for m in analyses_raw
        if m.key == "analysis"
    }

    # Content coverage
    content_rows = await session.execute(select(ContentItem))
    all_content = content_rows.scalars().all()
    content_by_doc: dict[str, dict[str, list]] = {}
    for item in all_content:
        d = content_by_doc.setdefault(
            item.document_id, {"notes": [], "quiz": [], "flashcards": []}
        )
        if item.type in d:
            d[item.type].append(item)

    # Proactive decks
    proactive_decks = [
        item for item in all_content
        if item.type == "flashcards"
        and isinstance(item.content, dict)
        and item.content.get("origin") == "proactive"
    ]

    # Due flashcard cards (existing cards on due concepts)
    due_names = {d["concept"] for d in due_concepts}
    due_cards = _find_due_flashcard_cards(all_content, due_names)

    # Session
    sess = await _get_session(session)

    # Behavioral understanding — the learner-model keys the agent already
    # maintains. Measurements (latency, dwell, hour histogram, session
    # outcomes) feed scoring; the LLM-written insights stay language.
    insights = await get_learner_insights(session) or {}
    patterns = await memory_store.read_memory(
        session, "user", "", behavior_store.PATTERNS_KEY
    ) or {}
    engagement = await memory_store.read_memory(
        session, "user", "", behavior_store.ENGAGEMENT_KEY
    ) or {}
    slow_concepts = await behavior_store.get_slow_concepts(session)

    # Computed helpers
    total_concepts = len(mastery)
    mastered_count = sum(
        1 for v in mastery.values()
        if v.get("mastery_pct") is not None and v["mastery_pct"] >= 0.7
    )
    welcome_back = _welcome_back(
        (profile.get("stats") or {}).get("last_interaction")
    )

    return UserContext(
        due_concepts=due_concepts,
        due_cards=due_cards,
        concept_mastery=mastery,
        weak_topics=weak_topics,
        profile=profile,
        documents=documents,
        content_by_doc=content_by_doc,
        analyses=analyses,
        proactive_decks=proactive_decks,
        session=sess,
        insights=insights,
        patterns=patterns if isinstance(patterns, dict) else {},
        engagement=engagement if isinstance(engagement, dict) else {},
        slow_concepts=slow_concepts,
        neglected_docs=_neglected_docs(documents, analyses, engagement),
        is_peak_hour=_is_peak_hour(patterns),
        due_count=len(due_concepts),
        mastered_count=mastered_count,
        total_concepts=total_concepts,
        welcome_back=welcome_back,
    )


def _neglected_docs(
    documents: dict[str, Document], analyses: dict[str, dict], engagement: dict
) -> list[str]:
    """Analyzed docs the learner has never opened (no recorded views).

    `documents` preserves the upload-desc order from build_context, so the
    neglected list does too. These are the docs quiz-gap/start-notes nudges
    should surface first — content that exists but never got attention.
    """
    viewed = {
        doc_id
        for doc_id, entry in (engagement.get("docs") or {}).items()
        if (entry or {}).get("views", 0) > 0
    }
    return [
        doc_id for doc_id in documents
        if doc_id in analyses and doc_id not in viewed
    ]


def _is_peak_hour(patterns: dict) -> bool:
    """True within ±1h of the learner's habitual study hour (UTC)."""
    best = patterns.get("best_study_hour") if isinstance(patterns, dict) else None
    if not isinstance(best, int) or isinstance(best, bool):
        return False
    now_hour = datetime.now(timezone.utc).hour
    return min((now_hour - best) % 24, (best - now_hour) % 24) <= 1


def _find_due_flashcard_cards(all_content, due_names: set[str]) -> list[dict]:
    """Find existing flashcard cards whose concept is due for review."""
    if not due_names:
        return []
    due_cards: list[dict] = []
    seen_ids: set[str] = set()
    for item in all_content:
        if item.type != "flashcards":
            continue
        for card in item.content.get("cards", []):
            concept = (card.get("concept") or "").strip()
            if concept in due_names and card.get("id") not in seen_ids:
                due_cards.append({
                    "id": card["id"],
                    "front": card["front"],
                    "back": card["back"],
                    "concept": concept,
                    "document_id": item.document_id,
                })
                seen_ids.add(card["id"])
    return due_cards


async def _get_session(session: AsyncSession) -> SessionContext | None:
    """Read the current session from agent_memory."""
    from ..agent.memory import read_memory

    data = await read_memory(session, "user", "", "session")
    if not data or not isinstance(data, dict):
        return None

    now = datetime.now(timezone.utc)
    actions_raw = data.get("actions") or []
    if not actions_raw:
        return None

    # Check if the session has expired (2h inactivity).
    try:
        last_ts = datetime.fromisoformat(actions_raw[-1]["ts"])
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        if (now - last_ts).total_seconds() > 2 * 3600:
            return None  # expired
    except (KeyError, ValueError):
        return None

    actions = [
        SessionAction(tool=a["tool"], doc_id=a.get("doc_id"), ts=a["ts"])
        for a in actions_raw[-10:]
    ]

    try:
        started = datetime.fromisoformat(data.get("started_at") or actions_raw[0]["ts"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        duration_mins = (now - started).total_seconds() / 60
    except (KeyError, ValueError):
        duration_mins = 0.0

    if duration_mins < 20:
        fatigue = "fresh"
    elif duration_mins < 50:
        fatigue = "focused"
    else:
        fatigue = "fatigued"

    return SessionContext(
        actions=actions,
        started_at=data.get("started_at"),
        duration_mins=duration_mins,
        fatigue_level=fatigue,
        dismissed_tools=set(data.get("dismissed_tools") or []),
    )


def _welcome_back(last_interaction: str | None) -> str | None:
    if not last_interaction:
        return None
    try:
        last = datetime.fromisoformat(last_interaction)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - last).days
        if days >= 7:
            return f"Welcome back — it's been {days} days"
        elif days >= 1:
            return f"Welcome back — it's been {days} day{'s' if days != 1 else ''}"
        return None
    except (ValueError, TypeError):
        return None
