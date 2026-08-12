"""Strategy implementations — one class per study tool recommendation.

Each strategy self-describes its applicability and scores itself against the
UserContext. Adding a new tool = create a strategy class + register it in
register_all(). The engine and other strategies never need to change.
"""

from __future__ import annotations

from ..context import UserContext, RecommendationResult


class OnboardingStrategy:
    """New user with no documents — recommend uploading."""
    name = "onboarding"
    category = "onboarding"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if ctx.documents:
            return None
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="onboarding",
            title="Upload a document to begin",
            rationale="Upload a PDF or text file and the agent will generate study materials for you.",
            score=1.0,
            dismissible=False,
        )


class DueReviewReadyStrategy:
    """FSRS-due concepts that have existing flashcard cards — review now."""
    name = "due_review_ready"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.due_concepts or not ctx.due_cards:
            return None

        # Find which doc has the most due cards.
        doc_counts: dict[str, int] = {}
        for card in ctx.due_cards:
            doc_counts[card["document_id"]] = doc_counts.get(card["document_id"], 0) + 1
        best_doc = max(doc_counts, key=doc_counts.get) if doc_counts else None
        doc_title = (
            ctx.documents[best_doc].filename
            if best_doc and best_doc in ctx.documents
            else "your documents"
        )

        count = len(ctx.due_concepts)
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="review_flashcards",
            title=f"Review {count} due concept{'s' if count != 1 else ''}",
            rationale=f"You have concepts due for spaced repetition from {doc_title}. Review them before you forget.",
            score=0.95,  # soft override — FSRS urgency
            document_id=best_doc,
            tab="flashcards",
            ready=True,
            deck={
                "title": f"Review: {count} due concepts",
                "cards": [
                    {"id": c["id"], "front": c["front"], "back": c["back"], "concept": c["concept"]}
                    for c in ctx.due_cards[:30]
                ],
            },
        )


class DueReviewGenerateStrategy:
    """FSRS-due concepts with no existing cards — generate flashcards."""
    name = "due_review_generate"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.due_concepts:
            return None
        # If cards exist, the DueReviewReady strategy handles it.
        if ctx.due_cards:
            return None

        doc_id = self._doc_with_most_due(ctx)
        doc = ctx.documents.get(doc_id) if doc_id else None
        title = doc.filename if doc else "your documents"
        count = len(ctx.due_concepts)

        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="generate_flashcards",
            title=f"Review {count} due concept{'s' if count != 1 else ''}",
            rationale=f"You have concepts due for spaced repetition from {title}. Generate flashcards to review them.",
            score=0.85,
            document_id=doc_id,
            tab="flashcards",
            ready=False,
        )

    def _doc_with_most_due(self, ctx: UserContext) -> str | None:
        due_set = {d["concept"] for d in ctx.due_concepts}
        best_doc = None
        best_count = 0
        for doc_id, analysis in ctx.analyses.items():
            if doc_id not in ctx.documents:
                continue
            concepts = {str(c) for c in (analysis.get("concepts") or [])}
            overlap = len(due_set & concepts)
            if overlap > best_count:
                best_count = overlap
                best_doc = doc_id
        return best_doc or next(iter(ctx.documents), None)


class ProactiveDeckStrategy:
    """An unseen proactive review deck exists — recommend it."""
    name = "proactive_deck"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.proactive_decks:
            return None
        deck = ctx.proactive_decks[0]
        doc = ctx.documents.get(deck.document_id)
        title_str = doc.filename if doc else "your documents"
        card_count = len(deck.content.get("cards", []))
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="review_flashcards",
            title="A review deck is ready for you",
            rationale=f"The agent prepared a {card_count}-card review deck from {title_str}. Jump right in.",
            score=0.80,
            document_id=deck.document_id,
            tab="flashcards",
            ready=True,
            content_id=deck.id,
        )


class QuizGapStrategy:
    """Document has notes but no quiz — test yourself."""
    name = "quiz_gap"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        for doc_id, content in ctx.content_by_doc.items():
            if content["notes"] and not content["quiz"] and doc_id in ctx.documents:
                doc = ctx.documents[doc_id]
                return RecommendationResult(
                    strategy_name=self.name,
                    category=self.category,
                    action="generate_quiz",
                    title=f"Test yourself on {doc.filename}",
                    rationale="You have study notes but haven't taken a quiz yet. Test your understanding.",
                    score=0.60,
                    document_id=doc_id,
                    tab="quiz",
                    ready=False,
                )
        return None


class StartNotesStrategy:
    """Document with no content at all — start with notes."""
    name = "start_notes"
    category = "organize"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        for doc_id in ctx.documents:
            content = ctx.content_by_doc.get(doc_id, {"notes": [], "quiz": [], "flashcards": []})
            if not any(content.values()):
                doc = ctx.documents[doc_id]
                return RecommendationResult(
                    strategy_name=self.name,
                    category=self.category,
                    action="generate_notes",
                    title=f"Start studying {doc.filename}",
                    rationale="This document has no study materials yet. Generate notes to get started.",
                    score=0.55,
                    document_id=doc_id,
                    tab="notes",
                    ready=False,
                )
        return None


class QuizStrategy:
    """General quiz recommendation with action chaining.

    Score is boosted if the user just completed flashcards (natural progression:
    flashcards → quiz to test application). Also boosted by exam proximity.
    """
    name = "quiz"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.documents:
            return None

        # Find a doc that has notes (can meaningfully quiz on it).
        target_doc = None
        for doc_id, content in ctx.content_by_doc.items():
            if content["notes"] and doc_id in ctx.documents:
                target_doc = doc_id
                break
        if not target_doc:
            target_doc = next(iter(ctx.documents), None)
        if not target_doc:
            return None

        doc = ctx.documents[target_doc]
        score = 0.30  # base utility
        rationale = "Test your comprehension with a quick quiz."

        # Action chaining: boost if flashcards were just completed.
        if ctx.session and ctx.session.actions:
            last = ctx.session.actions[-1]
            if last.tool == "flashcards":
                score += 0.40
                rationale = "Great job on flashcards! Test your application next."

        # Session momentum: boost if user is on a roll (multiple actions).
        if ctx.session and len(ctx.session.actions) >= 2:
            score += 0.10

        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="generate_quiz",
            title=f"Take a quiz on {doc.filename}",
            rationale=rationale,
            score=min(score, 1.0),
            document_id=target_doc,
            tab="quiz",
            ready=False,
        )


class FlashcardStrategy:
    """General flashcard recommendation with FSRS urgency boost."""
    name = "flashcards"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.documents:
            return None

        # Find a doc with notes.
        target_doc = None
        for doc_id, content in ctx.content_by_doc.items():
            if content["notes"] and doc_id in ctx.documents:
                target_doc = doc_id
                break
        if not target_doc:
            target_doc = next(iter(ctx.documents), None)
        if not target_doc:
            return None

        doc = ctx.documents[target_doc]
        score = 0.25  # base utility
        rationale = "Active recall with flashcards boosts retention."

        # FSRS urgency boost: more due concepts → higher score.
        if ctx.due_count > 0:
            score += min(ctx.due_count / 20, 0.30)  # up to +0.30
            rationale = f"{ctx.due_count} concepts due for review — flashcards will help."

        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="generate_flashcards",
            title=f"Flashcards: {doc.filename}",
            rationale=rationale,
            score=min(score, 1.0),
            document_id=target_doc,
            tab="flashcards",
            ready=False,
        )


class FallbackStrategy:
    """Low-priority fallback — quiz on most recent doc."""
    name = "fallback"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.documents:
            return None
        doc = next(iter(ctx.documents.values()))
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="generate_quiz",
            title=f"Take a quiz on {doc.filename}",
            rationale="Keep your knowledge sharp with a quick quiz.",
            score=0.10,
            document_id=doc.id,
            tab="quiz",
            ready=False,
        )


def register_all(engine):
    """Register all default strategies with the engine."""
    engine.register(OnboardingStrategy())
    engine.register(DueReviewReadyStrategy())
    engine.register(DueReviewGenerateStrategy())
    engine.register(ProactiveDeckStrategy())
    engine.register(QuizGapStrategy())
    engine.register(StartNotesStrategy())
    engine.register(QuizStrategy())
    engine.register(FlashcardStrategy())
    engine.register(FallbackStrategy())
