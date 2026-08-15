import { useEffect, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Check,
  RotateCcw,
  ArrowLeft,
  Loader2,
  Zap,
  Sparkles,
} from "lucide-react";
import { useRouterState } from "@tanstack/react-router";
import * as api from "../api/client";
import { track } from "../api/track";
import type { StudySession as StudySessionData } from "../api/client";

interface Props {
  session: StudySessionData;
  onExit: () => void;
}

export function StudySessionView({ session: initialSession, onExit }: Props) {
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [results, setResults] = useState<
    Record<string, "known" | "learning">
  >({});
  const [completed, setCompleted] = useState(false);

  // --- Timing + abandonment tracking ----------------------------------------
  const startedAtRef = useRef(Date.now());
  const shownAtRef = useRef(Date.now());
  const cardSecsRef = useRef<Record<string, number>>({});
  const completedRef = useRef(false);
  const resultsRef = useRef(results);
  resultsRef.current = results;

  useEffect(() => {
    shownAtRef.current = Date.now();
  }, [index]);

  // Leaving mid-session (exit button, browser back) is a signal — currently
  // it discards all results; at minimum the agent should know it happened.
  useEffect(() => {
    return () => {
      if (!completedRef.current && initialSession.cards.length > 0) {
        track("study.abandoned", {
          session_id: initialSession.id,
          completed: Object.keys(resultsRef.current).length,
          total: initialSession.cards.length,
        });
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const card = initialSession.cards[index];
  const isLast = index === initialSession.cards.length - 1;
  const knownCount = Object.values(results).filter(
    (r) => r === "known",
  ).length;
  const progressPct = Math.round(
    (Object.keys(results).length / initialSession.cards.length) * 100,
  );

  function markKnown(value: boolean) {
    if (!card) return;
    cardSecsRef.current[card.id] = Math.max(
      1,
      Math.round((Date.now() - shownAtRef.current) / 1000),
    );
    setResults((prev) => ({
      ...prev,
      [card.id]: value ? "known" : "learning",
    }));
    if (!isLast) {
      setFlipped(false);
      setIndex((i) => i + 1);
    } else {
      finishSession();
    }
  }

  async function finishSession() {
    setCompleted(true);
    completedRef.current = true;
    const allResults = { ...results };
    // Include the last card if it was just marked.
    const reviewData = initialSession.cards
      .filter((c) => allResults[c.id])
      .map((c) => ({
        card_id: c.id,
        known: allResults[c.id] === "known",
        concept: c.concept,
        content_id: c.content_id,
        secs: cardSecsRef.current[c.id] ?? null,
      }));
    if (reviewData.length > 0) {
      await api.submitSessionReview(
        initialSession.id,
        reviewData,
        Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000)),
      );
    }
  }

  if (completed) {
    const total = initialSession.cards.length;
    const correct = knownCount;
    const pct = Math.round((correct / total) * 100);
    return (
      <div className="study-complete">
        <div className="study-complete-card">
          <div className={`study-score-ring ${pct >= 70 ? "pass" : "fail"}`}>
            {pct}%
          </div>
          <h2>Session complete</h2>
          <p className="study-score-detail">
            {correct}/{total} correct · {initialSession.rationale}
          </p>
          <p className="study-score-msg">
            {pct >= 85
              ? "Excellent! Your next session will include more new material."
              : pct >= 70
                ? "Nice work! You're in the optimal learning zone."
                : "Keep studying — your next session will focus on review."}
          </p>
          <button className="primary" onClick={onExit}>
            Done
          </button>
        </div>
      </div>
    );
  }

  if (!card) {
    return (
      <div className="study-session-page">
        <div className="empty">No cards in this session.</div>
      </div>
    );
  }

  const reviewCount = initialSession.mix.review;
  const newCount = initialSession.mix.new;

  return (
    <div className="study-session-page">
      <div className="study-header">
        <button className="ghost icon-btn" onClick={onExit} title="Exit">
          <ArrowLeft size={20} />
        </button>
        <span className="study-progress-text">
          Card {index + 1} of {initialSession.cards.length}
        </span>
        <div className="study-tags">
          {reviewCount > 0 && (
            <span className="study-tag review">
              <Zap size={11} /> {reviewCount} review
            </span>
          )}
          {newCount > 0 && (
            <span className="study-tag new">
              <Sparkles size={11} /> {newCount} new
            </span>
          )}
        </div>
      </div>

      <div className="study-progress-bar">
        <div
          className="study-progress-fill"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <div className="card-deck">
        <div className="flashcard-scene">
          <div
            className={`flashcard ${flipped ? "flipped" : ""}`}
            onClick={() => {
              if (!flipped) {
                track("flashcard.flipped", {
                  card_id: card.id,
                  concept: card.concept,
                  front_secs: Math.max(
                    1,
                    Math.round((Date.now() - shownAtRef.current) / 1000),
                  ),
                });
              }
              setFlipped((f) => !f);
            }}
          >
            <div className="card-face front">
              <span className="face-pill">
                {card.source === "review" ? "Review" : "New"} · click to flip
              </span>
              <div className="card-text">{card.front}</div>
            </div>
            <div className="card-face back">
              <span className="face-pill">Answer · click to flip back</span>
              <div className="card-text">{card.back}</div>
            </div>
          </div>
        </div>

        <div className="card-nav">
          <button
            className="icon-btn ghost"
            onClick={() => {
              setFlipped(false);
              setIndex((i) => Math.max(i - 1, 0));
            }}
            disabled={index === 0}
          >
            <ChevronLeft size={20} />
          </button>
          <span className="card-counter">
            {knownCount} / {initialSession.cards.length} known
          </span>
          <button
            className="icon-btn ghost"
            onClick={() => {
              setFlipped(false);
              setIndex((i) => Math.min(i + 1, initialSession.cards.length - 1));
            }}
            disabled={isLast}
          >
            <ChevronRight size={20} />
          </button>
        </div>

        <div className="card-nav">
          <button onClick={() => markKnown(false)}>
            <RotateCcw size={15} />
            Still learning
          </button>
          <button className="primary" onClick={() => markKnown(true)}>
            <Check size={16} />
            I know this
          </button>
        </div>
      </div>
    </div>
  );
}

/** Loading wrapper for study session — fetches the session then renders the player. */
export function StudySessionLoader({ onExit }: { onExit: () => void }) {
  const [session, setSession] = useState<StudySessionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Module scope via /study?module=<id> (plan deep links use this).
  const search = useRouterState({
    select: (s) => s.location.search as { module?: string },
  });
  const moduleId = search.module;

  // Fetch session on mount.
  useState(() => {
    api
      .startStudySession(
        "flashcards",
        20,
        moduleId ? "module" : "global",
        undefined,
        moduleId,
      )
      .then((s) => setSession(s))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  });

  if (loading) {
    return (
      <div className="study-session-page">
        <div className="loading">
          <Loader2 size={20} className="spinner" />
          Composing your study session…
        </div>
      </div>
    );
  }
  if (error || !session) {
    return (
      <div className="study-session-page">
        <div className="error">
          {error || "Failed to start session."}
        </div>
        <button className="ghost" onClick={onExit}>Back</button>
      </div>
    );
  }
  return <StudySessionView session={session} onExit={onExit} />;
}
