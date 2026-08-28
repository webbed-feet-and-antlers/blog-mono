"""Strategy implementations — one class per study tool recommendation.

Each strategy self-describes its applicability and scores itself against the
UserContext. Adding a new tool = create a strategy class + register it in
register_all(). The engine and other strategies never need to change.
"""

from __future__ import annotations

from ..context import UserContext, RecommendationResult
from datetime import datetime, timezone


def _format_tilt(ctx: UserContext) -> bool:
    """True when the learner's measured time/energy budget favors short formats.

    Grounded in study_patterns: quizzes consistently running long (>2 min on
    average) or more abandoned than completed sessions mean flashcards fit
    the moment better than another quiz.
    """
    patterns = ctx.patterns if isinstance(ctx.patterns, dict) else {}
    avg_secs = patterns.get("avg_quiz_duration_secs")
    sessions = patterns.get("sessions") or {}
    abandoned = int(sessions.get("abandoned", 0) or 0)
    completed = int(sessions.get("completed", 0) or 0)
    long_quizzes = isinstance(avg_secs, (int, float)) and avg_secs > 120
    return long_quizzes or abandoned > completed


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
        rationale = (
            f"You have concepts due for spaced repetition from {doc_title}. "
            "Review them before you forget."
        )

        # Rank the deck by predicted failure risk — highest first — with
        # recently-active concepts tiered ahead of long-idle ones. Active
        # material is in the learner's orbit (they'll face it again); idle
        # due concepts are mostly abandoned (the EdNet replay: 82% of
        # risk-ranked due concepts never attempted again within 7 days).
        # The recommend replay scores exactly this ordering (README #10).
        from ...agent.fsrs_scheduler import failure_risk, is_recently_active

        now = datetime.now(timezone.utc)
        fsrs_by_concept = {d["concept"]: d.get("fsrs") for d in ctx.due_concepts}
        last_by_concept = {
            d["concept"]: d.get("last_attempt_ts") for d in ctx.due_concepts
        }
        ranked = sorted(
            ctx.due_cards,
            key=lambda c: (
                is_recently_active(last_by_concept.get(c["concept"]), now),
                failure_risk(
                    ctx.concept_mastery.get(c["concept"]),
                    fsrs_by_concept.get(c["concept"]),
                ),
            ),
            reverse=True,
        )

        # Behavioral enrichment: a due concept the learner also answers
        # slowly is the one to call out.
        slow_by_concept = {c["concept"]: c for c in ctx.slow_concepts}
        slow_due = [
            slow_by_concept[d["concept"]]
            for d in ctx.due_concepts
            if d["concept"] in slow_by_concept
        ]
        if slow_due:
            first = slow_due[0]
            more = f" (+{len(slow_due) - 1} more)" if len(slow_due) > 1 else ""
            rationale += (
                f" {first['concept']} is one you recall slowly"
                f" (~{first['avg_secs']}s avg){more}."
            )
        elif ranked:
            top = ranked[0]["concept"]
            rationale += f" {top} is your most at-risk concept right now."

        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="review_flashcards",
            title=f"Review {count} due concept{'s' if count != 1 else ''}",
            rationale=rationale,
            score=0.95,  # soft override — FSRS urgency
            document_id=best_doc,
            tab="flashcards",
            ready=True,
            deck={
                "title": f"Review: {count} due concepts",
                "cards": [
                    {"id": c["id"], "front": c["front"], "back": c["back"], "concept": c["concept"]}
                    for c in ranked[:30]
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


class WeakSpotStrategy:
    """Concepts the learner answers slowly — recommend a targeted quiz.

    Grounded in behavioral measurements: per-concept answer latency (from
    quiz/flashcard timings) plus weak-topic flags. Defers to spaced
    repetition when actual reviews are due — DueReviewReady owns that moment.
    """
    name = "weak_spot"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if ctx.due_cards:
            return None  # FSRS-due reviews win this moment

        weak_names = {c["concept"] for c in ctx.slow_concepts}
        corroborated = bool(ctx.weak_topics)
        # Two independent slow concepts, or one corroborated by weak-topic flags.
        if len(weak_names) < 2 and not (weak_names and corroborated):
            return None

        doc_id = self._doc_covering_weakness(ctx, weak_names)
        if not doc_id:
            return None
        doc = ctx.documents[doc_id]

        named = ctx.slow_concepts[:2]
        names = " and ".join(c["concept"] for c in named)
        rationale = (
            f"You recall {names} slowly (~{named[0]['avg_secs']}s avg) — "
            f"a targeted quiz would tighten that."
        )

        score = 0.55 + 0.05 * min(len(ctx.slow_concepts), 4)
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="generate_quiz",
            title=f"Targeted quiz on your slow concepts",
            rationale=rationale,
            score=min(score, 0.75),
            document_id=doc_id,
            tab="quiz",
            ready=False,
        )

    def _doc_covering_weakness(self, ctx: UserContext, slow_names: set[str]) -> str | None:
        """The doc whose analysis covers the most slow/weak concepts."""
        weak_set = set(slow_names)
        for entry in ctx.weak_topics:
            topic = entry.get("topic")
            if topic:
                weak_set.add(str(topic))
        best_doc = None
        best_count = 0
        for doc_id, analysis in ctx.analyses.items():
            if doc_id not in ctx.documents:
                continue
            concepts = {str(c) for c in (analysis.get("concepts") or [])}
            overlap = len(weak_set & concepts)
            if overlap > best_count:
                best_count = overlap
                best_doc = doc_id
        return best_doc


class PlanTodayStrategy:
    """Today's study-plan item — the plan speaks on the home card.

    The planner already paced each module toward its exam; this makes the
    recommendation panel and the plan one voice instead of two agents
    disagreeing. Scored just below FSRS-due reviews (memory decay is the
    one thing that outranks a commitment) and above everything else.
    """
    name = "plan_today"
    category = "learn"

    # Plan item type → frontend action. review_* routes to the study
    # session composer; take_quiz/read_document deep-link to the doc.
    _ACTIONS = {
        "review_concepts": ("review_flashcards", None, True),
        "review_deck": ("review_flashcards", None, True),
        "take_quiz": ("view_document", "quiz", True),
        "generate_quiz": ("generate_quiz", "quiz", False),
        "generate_flashcards": ("generate_flashcards", "flashcards", False),
        "read_document": ("view_document", "document", True),
    }

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.plan_today:
            return None
        item = ctx.plan_today[0]
        action, tab, ready = self._ACTIONS.get(
            item.get("type"), ("view_document", "document", True)
        )
        title = item.get("title") or "Today's plan"
        module_title = item.get("module_title", "your module")
        rationale = item.get("rationale") or "Scheduled in your study plan."
        estimate = item.get("estimate_mins")
        if estimate:
            rationale += f" (~{estimate} min)"
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action=action,
            title=f"Today's plan: {title}",
            rationale=f"{module_title} — {rationale}",
            score=0.92,  # top authority under FSRS-due reviews
            document_id=(item.get("target") or {}).get("document_id"),
            tab=tab,
            ready=ready,
        )


class ProactiveDeckStrategy:
    """An unseen proactive review deck exists — recommend the most relevant.

    Decks are ranked by overlap with the learner's slow/weak concepts
    rather than taken in list order: the deck that targets the actual
    weakness is the one worth a click.
    """
    name = "proactive_deck"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        if not ctx.proactive_decks:
            return None
        weak_set = {c["concept"] for c in ctx.slow_concepts}
        for entry in ctx.weak_topics:
            topic = entry.get("topic")
            if topic:
                weak_set.add(str(topic))

        best_deck = None
        best_overlap = -1
        for deck in ctx.proactive_decks:
            concepts = {
                (card.get("concept") or "").strip()
                for card in deck.content.get("cards", [])
            }
            overlap = len(concepts & weak_set)
            if overlap > best_overlap:
                best_overlap = overlap
                best_deck = deck
        deck = best_deck
        if deck is None:
            return None

        doc = ctx.documents.get(deck.document_id)
        title_str = doc.filename if doc else "your documents"
        card_count = len(deck.content.get("cards", []))
        if best_overlap > 0:
            rationale = (
                f"The agent prepared a {card_count}-card review deck targeting "
                f"{best_overlap} of your weak concepts. Jump right in."
            )
        else:
            rationale = (
                f"The agent prepared a {card_count}-card review deck from "
                f"{title_str}. Jump right in."
            )
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="review_flashcards",
            title="A review deck is ready for you",
            rationale=rationale,
            score=0.80,
            document_id=deck.document_id,
            tab="flashcards",
            ready=True,
            content_id=deck.id,
        )


class QuizGapStrategy:
    """Document has notes but no quiz — test yourself.

    Never-opened docs (neglected, per engagement) jump the queue: content
    that exists but never got attention is the likelier blind spot.
    """
    name = "quiz_gap"
    category = "practice"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        neglected = set(ctx.neglected_docs)
        ordered = [d for d in ctx.neglected_docs if d in ctx.content_by_doc]
        ordered += [d for d in ctx.content_by_doc if d not in neglected]
        for doc_id in ordered:
            content = ctx.content_by_doc[doc_id]
            if content["notes"] and not content["quiz"] and doc_id in ctx.documents:
                doc = ctx.documents[doc_id]
                if doc_id in neglected:
                    rationale = (
                        f"You haven't opened {doc.filename} since uploading it — "
                        "a quiz would tell you where you stand."
                    )
                else:
                    rationale = (
                        "You have study notes but haven't taken a quiz yet. "
                        "Test your understanding."
                    )
                return RecommendationResult(
                    strategy_name=self.name,
                    category=self.category,
                    action="generate_quiz",
                    title=f"Test yourself on {doc.filename}",
                    rationale=rationale,
                    score=0.60,
                    document_id=doc_id,
                    tab="quiz",
                    ready=False,
                )
        return None


class StartNotesStrategy:
    """Document with no content at all — start with notes.

    Never-opened docs (neglected, per engagement) jump the queue.
    """
    name = "start_notes"
    category = "organize"

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        neglected = set(ctx.neglected_docs)
        ordered = list(ctx.neglected_docs)
        ordered += [d for d in ctx.documents if d not in neglected]
        for doc_id in ordered:
            content = ctx.content_by_doc.get(doc_id, {"notes": [], "quiz": [], "flashcards": []})
            if not any(content.values()) and doc_id in ctx.documents:
                doc = ctx.documents[doc_id]
                if doc_id in neglected:
                    rationale = (
                        "You uploaded this but haven't opened it yet — "
                        "generate notes to get started."
                    )
                else:
                    rationale = (
                        "This document has no study materials yet. "
                        "Generate notes to get started."
                    )
                return RecommendationResult(
                    strategy_name=self.name,
                    category=self.category,
                    action="generate_notes",
                    title=f"Start studying {doc.filename}",
                    rationale=rationale,
                    score=0.55,
                    document_id=doc_id,
                    tab="notes",
                    ready=False,
                )
        return None


class RevisitStrategy:
    """A previously-read document untouched for days — revisit it.

    Engagement remembers when each doc was last opened; staleness plus
    prior attention (they chose to read it once) is a gentle, grounded
    nudge. Scores below gap strategies — a revisit is nice, a gap is work.
    """
    name = "revisit"
    category = "learn"
    REVISIT_DAYS = 5

    def evaluate(self, ctx: UserContext) -> RecommendationResult | None:
        now = datetime.now(timezone.utc)
        docs = ctx.engagement.get("docs") or {}
        best = None  # (days_stale, doc_id)
        for doc_id, entry in docs.items():
            entry = entry or {}
            if (entry.get("views") or 0) <= 0 or doc_id not in ctx.documents:
                continue
            last = entry.get("last_viewed")
            if not last:
                continue
            try:
                last_dt = datetime.fromisoformat(str(last))
            except ValueError:
                continue
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            days = (now - last_dt).days
            if days >= self.REVISIT_DAYS and (best is None or days > best[0]):
                best = (days, doc_id)
        if best is None:
            return None
        days, doc_id = best
        doc = ctx.documents[doc_id]
        return RecommendationResult(
            strategy_name=self.name,
            category=self.category,
            action="view_document",
            title=f"Revisit {doc.filename}",
            rationale=(
                f"You haven't revisited this in {days} days — "
                "a quick re-read keeps it fresh."
            ),
            score=0.40,
            document_id=doc_id,
            tab="document",
            ready=True,
        )


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

        # Format tilt: measured quiz duration/abandonment favor flashcards.
        if _format_tilt(ctx):
            score -= 0.05

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

        # Format tilt: measured quiz duration/abandonment favor flashcards.
        if _format_tilt(ctx):
            score += 0.10
            rationale += " And flashcards fit the time you have — your quizzes have been running long."

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
    engine.register(PlanTodayStrategy())
    engine.register(WeakSpotStrategy())
    engine.register(ProactiveDeckStrategy())
    engine.register(QuizGapStrategy())
    engine.register(StartNotesStrategy())
    engine.register(RevisitStrategy())
    engine.register(QuizStrategy())
    engine.register(FlashcardStrategy())
    engine.register(FallbackStrategy())
