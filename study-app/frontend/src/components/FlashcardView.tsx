import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  Check,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import * as api from "../api/client";
import type { FlashcardContent } from "../types";

interface Props {
  contentId: string;
  content: FlashcardContent;
}

export function FlashcardView({ contentId, content }: Props) {
  const isProactive = (content as any).origin === "proactive";
  const queryClient = useQueryClient();

  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  // Track per-card review state: "known" | "learning" | undefined (unreviewed).
  // We send all reviewed cards to the backend on unmount so mastery persists.
  const [reviews, setReviews] = useState<Record<string, "known" | "learning">>(
    {},
  );
  const reviewsRef = useRef(reviews);
  reviewsRef.current = reviews;

  const card = content.cards[index];
  const isLast = index === content.cards.length - 1;
  const knownCount = Object.values(reviews).filter((r) => r === "known").length;
  const progressPct = Math.round((knownCount / content.cards.length) * 100);

  const reviewMutation = useMutation({
    mutationFn: (results: { card_id: string; known: boolean; concept: string }[]) =>
      api.submitFlashcardReview(contentId, results),
    onSuccess: () => {
      // Invalidate memory queries so mastery-driven features refresh.
      queryClient.invalidateQueries({ queryKey: ["memory"] });
      queryClient.invalidateQueries({ queryKey: ["proactive-decks"] });
    },
  });

  // POST all accumulated reviews when the component unmounts (user navigates
  // away or a new deck loads). This makes flashcard mastery durable.
  useEffect(() => {
    return () => {
      const reviewed = reviewsRef.current;
      const results = content.cards
        .filter((c) => reviewed[c.id])
        .map((c) => ({
          card_id: c.id,
          known: reviewed[c.id] === "known",
          concept: c.concept ?? "",
        }));
      if (results.length > 0) {
        reviewMutation.mutate(results);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentId]);

  function next() {
    setFlipped(false);
    setIndex((i) => Math.min(i + 1, content.cards.length - 1));
  }
  function prev() {
    setFlipped(false);
    setIndex((i) => Math.max(i - 1, 0));
  }
  function markKnown(value: boolean) {
    setReviews((prev) => ({
      ...prev,
      [card.id]: value ? "known" : "learning",
    }));
    if (!isLast) next();
  }

  return (
    <div className="card-deck">
      {isProactive && (
        <div className="deck-origin-pill">
          <Sparkles size={13} />
          Agent-prepared review
        </div>
      )}
      <div className="card-progress">
        <div
          className="card-progress-fill"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <div className="flashcard-scene">
        <div
          className={`flashcard ${flipped ? "flipped" : ""}`}
          onClick={() => setFlipped((f) => !f)}
        >
          <div className="card-face front">
            <span className="face-pill">Question</span>
            <div className="card-text">{card.front}</div>
            <span className="card-hint">Click to flip</span>
          </div>
          <div className="card-face back">
            <span className="face-pill">Answer</span>
            <div className="card-text">{card.back}</div>
            <span className="card-hint">Click to flip back</span>
          </div>
        </div>
      </div>

      <div className="card-nav">
        <button className="icon-btn ghost" onClick={prev} disabled={index === 0}>
          <ChevronLeft size={20} />
        </button>
        <span className="card-counter">
          {index + 1} / {content.cards.length}
        </span>
        <button className="icon-btn ghost" onClick={next} disabled={isLast}>
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

      <div className="card-counter" style={{ marginTop: 4 }}>
        {knownCount} of {content.cards.length} mastered
      </div>
    </div>
  );
}
