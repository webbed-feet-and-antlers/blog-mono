"""Recommendation endpoint — the agent guides the user.

GET /api/recommend composes all available signals (FSRS-due concepts, mastery,
learner profile, content coverage, recency) and returns what the user should do
right now. The decision engine is deterministic (no LLM) so it's instant.

If existing content covers the need (e.g. flashcard cards on due concepts),
the recommendation bundles a ready-to-go deck. Otherwise, action="generate_*"
and the frontend triggers the streaming generation flow.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import memory as memory_store
from ..db import get_session
from ..models import ContentItem, Document

router = APIRouter(prefix="/api/recommend", tags=["recommend"])


@router.get("")
async def recommend(session: AsyncSession = Depends(get_session)):
    """Return the agent's recommendation for what to study next."""
    # --- Gather all signals ---
    due_concepts = await memory_store.get_due_concepts(session)
    due_names = {d["concept"] for d in due_concepts}
    profile = await memory_store.get_learner_profile(session)
    mastery = await memory_store.get_concept_mastery(session)
    proactive_decks = await _get_proactive_decks(session)

    # Enumerate documents and their analyses.
    doc_analyses = await memory_store.list_memory(session, scope="doc")
    docs_by_id: dict[str, dict] = {}
    for m in doc_analyses:
        if m.key == "analysis":
            docs_by_id[m.ref_id] = m.value or {}

    # Load actual documents (for titles).
    doc_rows = await session.execute(select(Document).order_by(Document.uploaded_at.desc()))
    documents = {d.id: d for d in doc_rows.scalars().all()}

    # Check content coverage per doc (what exists: notes? quiz? flashcards?).
    content_rows = await session.execute(select(ContentItem))
    all_content = content_rows.scalars().all()
    content_by_doc: dict[str, dict[str, list]] = {}
    for item in all_content:
        d = content_by_doc.setdefault(item.document_id, {"notes": [], "quiz": [], "flashcards": []})
        if item.type in d:
            d[item.type].append(item)

    # Find existing flashcard cards on due concepts (for reuse).
    due_cards = _find_due_flashcard_cards(all_content, due_names)

    # --- Build context ---
    stats = profile.get("stats") or {}
    total_concepts = len(mastery)
    mastered_count = sum(
        1 for v in mastery.values()
        if v.get("mastery_pct") is not None and v["mastery_pct"] >= 0.7
    )
    welcome_back = _welcome_back(stats.get("last_interaction"))

    context = {
        "due_count": len(due_concepts),
        "learner_level": profile.get("learner_level", "unknown"),
        "total_concepts": total_concepts,
        "mastered_count": mastered_count,
        "welcome_back": welcome_back,
        "total_quizzes": stats.get("total_quizzes", 0),
    }

    # --- Decision engine (priority-ordered) ---
    alternatives: list[dict] = []
    primary = _decide(
        due_concepts=due_concepts,
        due_cards=due_cards,
        proactive_decks=proactive_decks,
        docs_by_id=docs_by_id,
        documents=documents,
        content_by_doc=content_by_doc,
        profile=profile,
    )

    # Build alternatives from lower-priority options.
    alternatives = _build_alternatives(
        due_concepts=due_concepts,
        proactive_decks=proactive_decks,
        docs_by_id=docs_by_id,
        documents=documents,
        content_by_doc=content_by_doc,
        primary_action=primary["action"],
    )

    return {"primary": primary, "alternatives": alternatives[:3], "context": context}


# --- Decision helpers ---


def _decide(
    *,
    due_concepts: list[dict],
    due_cards: list[dict],
    proactive_decks: list,
    docs_by_id: dict[str, dict],
    documents: dict[str, Document],
    content_by_doc: dict[str, dict[str, list]],
    profile: dict,
) -> dict:
    """Pick the best recommendation using priority-ordered rules."""

    # 0. No documents → onboarding.
    if not documents:
        return {
            "action": "onboarding",
            "title": "Upload a document to begin",
            "rationale": "Upload a PDF or text file and the agent will generate study materials for you.",
            "document_id": None,
            "tab": None,
            "ready": False,
            "deck": None,
        }

    # 1. Due concepts + existing cards cover them → review (ready=true).
    if due_concepts and due_cards:
        # Find which doc most due cards belong to.
        doc_counts: dict[str, int] = {}
        for card in due_cards:
            doc_counts[card["document_id"]] = doc_counts.get(card["document_id"], 0) + 1
        best_doc = max(doc_counts, key=doc_counts.get) if doc_counts else None
        doc_title = documents[best_doc].filename if best_doc and best_doc in documents else "your documents"
        return {
            "action": "review_flashcards",
            "title": f"Review {len(due_concepts)} due concept{'s' if len(due_concepts) != 1 else ''}",
            "rationale": f"You have concepts due for spaced repetition from {doc_title}. Review them before you forget.",
            "document_id": best_doc,
            "tab": "flashcards",
            "ready": True,
            "deck": {
                "title": f"Review: {len(due_concepts)} due concepts",
                "cards": [{"id": c["id"], "front": c["front"], "back": c["back"], "concept": c["concept"]} for c in due_cards[:30]],
            },
        }

    # 2. Due concepts but no existing cards → generate flashcards (ready=false).
    if due_concepts:
        # Pick the doc with the most due concepts.
        doc_due = _doc_with_most_due(due_concepts, docs_by_id, documents)
        return {
            "action": "generate_flashcards",
            "title": f"Review {len(due_concepts)} due concept{'s' if len(due_concepts) != 1 else ''}",
            "rationale": f"You have concepts due for spaced repetition{doc_due['suffix']}. Generate flashcards to review them.",
            "document_id": doc_due["doc_id"],
            "tab": "flashcards",
            "ready": False,
            "deck": None,
        }

    # 3. Unseen proactive deck → review (ready=true).
    if proactive_decks:
        deck = proactive_decks[0]
        doc_title = documents.get(deck.document_id, None)
        title_str = doc_title.filename if doc_title else "your documents"
        card_count = len(deck.content.get("cards", []))
        return {
            "action": "review_flashcards",
            "title": "A review deck is ready for you",
            "rationale": f"The agent prepared a {card_count}-card review deck from {title_str}. Jump right in.",
            "document_id": deck.document_id,
            "tab": "flashcards",
            "ready": True,
            "deck": None,  # the deck exists as a ContentItem, frontend will find it
            "content_id": deck.id,
        }

    # 4. Doc has notes but no quiz → test yourself.
    for doc_id, content in content_by_doc.items():
        if content["notes"] and not content["quiz"] and doc_id in documents:
            doc = documents[doc_id]
            return {
                "action": "generate_quiz",
                "title": f"Test yourself on {doc.filename}",
                "rationale": "You have study notes but haven't taken a quiz yet. Test your understanding.",
                "document_id": doc_id,
                "tab": "quiz",
                "ready": False,
                "deck": None,
            }

    # 5. Doc with no content at all → start with notes.
    for doc_id in documents:
        content = content_by_doc.get(doc_id, {"notes": [], "quiz": [], "flashcards": []})
        if not content["notes"] and not content["quiz"] and not content["flashcards"]:
            doc = documents[doc_id]
            return {
                "action": "generate_notes",
                "title": f"Start studying {doc.filename}",
                "rationale": "This document has no study materials yet. Generate notes to get started.",
                "document_id": doc_id,
                "tab": "notes",
                "ready": False,
                "deck": None,
            }

    # 6. Fallback — recommend a quiz on the most-recent doc.
    latest_doc = next(iter(documents.values()), None)
    if latest_doc:
        return {
            "action": "generate_quiz",
            "title": f"Take a quiz on {latest_doc.filename}",
            "rationale": "Keep your knowledge sharp with a quick quiz.",
            "document_id": latest_doc.id,
            "tab": "quiz",
            "ready": False,
            "deck": None,
        }

    return {
        "action": "onboarding",
        "title": "Upload a document to begin",
        "rationale": "Upload a PDF or text file to start studying.",
        "document_id": None,
        "tab": None,
        "ready": False,
        "deck": None,
    }


def _build_alternatives(
    *,
    due_concepts: list[dict],
    proactive_decks: list,
    docs_by_id: dict[str, dict],
    documents: dict[str, Document],
    content_by_doc: dict[str, dict[str, list]],
    primary_action: str,
) -> list[dict]:
    """Build 1-3 lower-priority alternatives (excluding the primary action)."""
    alts: list[dict] = []

    # If primary isn't quiz-related, suggest a quiz on a doc that has notes.
    if primary_action not in ("generate_quiz",):
        for doc_id, content in content_by_doc.items():
            if content["notes"] and doc_id in documents:
                doc = documents[doc_id]
                alts.append({
                    "action": "generate_quiz",
                    "title": f"Quiz: {doc.filename}",
                    "rationale": "Test your understanding with a quick quiz.",
                    "document_id": doc_id,
                    "tab": "quiz",
                    "ready": False,
                })
                break

    # If primary isn't flashcards, suggest flashcards on a doc.
    if primary_action not in ("review_flashcards", "generate_flashcards"):
        for doc_id, content in content_by_doc.items():
            if content["notes"] and doc_id in documents:
                doc = documents[doc_id]
                alts.append({
                    "action": "generate_flashcards",
                    "title": f"Flashcards: {doc.filename}",
                    "rationale": "Active recall with flashcards boosts retention.",
                    "document_id": doc_id,
                    "tab": "flashcards",
                    "ready": False,
                })
                break

    # If there's a second document, suggest notes or study on it.
    doc_ids = list(documents.keys())
    if len(doc_ids) > 1:
        second_doc = documents[doc_ids[1]]
        alts.append({
            "action": "view_document",
            "title": f"Study {second_doc.filename}",
            "rationale": "You haven't looked at this document recently.",
            "document_id": second_doc.id,
            "tab": "document",
            "ready": True,
        })

    return alts


def _find_due_flashcard_cards(
    all_content: list[ContentItem], due_names: set[str]
) -> list[dict]:
    """Find existing flashcard cards whose concept is due for review."""
    if not due_names:
        return []
    due_cards: list[dict] = []
    seen_card_ids: set[str] = set()
    for item in all_content:
        if item.type != "flashcards":
            continue
        for card in item.content.get("cards", []):
            concept = (card.get("concept") or "").strip()
            if concept in due_names and card.get("id") not in seen_card_ids:
                due_cards.append({
                    "id": card["id"],
                    "front": card["front"],
                    "back": card["back"],
                    "concept": concept,
                    "document_id": item.document_id,
                })
                seen_card_ids.add(card["id"])
    return due_cards


def _doc_with_most_due(
    due_concepts: list[dict],
    docs_by_id: dict[str, dict],
    documents: dict[str, Document],
) -> dict:
    """Find the document whose concepts overlap most with due concepts."""
    due_set = {d["concept"] for d in due_concepts}
    best_doc = None
    best_count = 0
    for doc_id, analysis in docs_by_id.items():
        if doc_id not in documents:
            continue
        doc_concepts = {str(c) for c in (analysis.get("concepts") or [])}
        overlap = len(due_set & doc_concepts)
        if overlap > best_count:
            best_count = overlap
            best_doc = doc_id
    if best_doc is None:
        best_doc = next(iter(documents), None)
    title = documents[best_doc].filename if best_doc and best_doc in documents else ""
    suffix = f" from {title}" if title else ""
    return {"doc_id": best_doc, "suffix": suffix}


def _welcome_back(last_interaction: str | None) -> str | None:
    """Return a welcome-back message if the user has been away."""
    if not last_interaction:
        return None
    try:
        last = datetime.fromisoformat(last_interaction)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (now - last).days
        if days >= 7:
            return f"Welcome back — it's been {days} days since you last studied"
        elif days >= 1:
            return f"Welcome back — it's been {days} day{'s' if days != 1 else ''} since you last studied"
        return None
    except (ValueError, TypeError):
        return None


async def _get_proactive_decks(session: AsyncSession) -> list[ContentItem]:
    """Return proactive flashcard decks, newest first."""
    result = await session.execute(
        select(ContentItem)
        .where(ContentItem.type == "flashcards")
        .order_by(ContentItem.created_at.desc())
    )
    return [
        item
        for item in result.scalars().all()
        if isinstance(item.content, dict) and item.content.get("origin") == "proactive"
    ]
