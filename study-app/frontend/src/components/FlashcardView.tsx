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
import { track } from "../api/track";
import type { FlashcardContent } from "../types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

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

  // --- Per-card timing (behavioral difficulty signal) -----------------------
  const shownAtRef = useRef(Date.now());
  const cardSecsRef = useRef<Record<string, number>>({});
  useEffect(() => {
    shownAtRef.current = Date.now();
  }, [index]);

  const card = content.cards[index];
  const isLast = index === content.cards.length - 1;
  const knownCount = Object.values(reviews).filter((r) => r === "known").length;
  const progressPct = Math.round((knownCount / content.cards.length) * 100);

  const reviewMutation = useMutation({
    mutationFn: (results: {
      card_id: string;
      known: boolean;
      concept: string;
      secs?: number | null;
    }[]) => api.submitFlashcardReview(contentId, results),
    onSuccess: () => {
      // Invalidate memory queries so mastery-driven features refresh.
      queryClient.invalidateQueries({ queryKey: ["memory"] });
      queryClient.invalidateQueries({ queryKey: ["learner-profile"] });
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
          secs: cardSecsRef.current[c.id] ?? null,
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
    cardSecsRef.current[card.id] = Math.max(
      1,
      Math.round((Date.now() - shownAtRef.current) / 1000),
    );
    setReviews((prev) => ({
      ...prev,
      [card.id]: value ? "known" : "learning",
    }));
    if (!isLast) next();
  }

  return (
    <div className="card-deck">
      {isProactive && (
        <Badge className="mb-3.5 gap-1.5 bg-accent px-2.5 py-1 text-[0.72rem] text-primary">
          <Sparkles size={13} />
          Agent-prepared review
        </Badge>
      )}
      <Progress
        value={progressPct}
        className="h-1.5 max-w-[580px] bg-secondary [&_[data-slot=progress-indicator]]:bg-ok"
      />

      <div className="flashcard-scene">
        <div
          className={`flashcard ${flipped ? "flipped" : ""}`}
          onClick={() => {
            if (!flipped) {
              track("flashcard.flipped", {
                card_id: card.id,
                concept: card.concept ?? null,
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
        <Button
          variant="ghost"
          size="icon"
          onClick={prev}
          disabled={index === 0}
          aria-label="Previous card"
        >
          <ChevronLeft size={20} />
        </Button>
        <span className="card-counter">
          {index + 1} / {content.cards.length}
        </span>
        <Button
          variant="ghost"
          size="icon"
          onClick={next}
          disabled={isLast}
          aria-label="Next card"
        >
          <ChevronRight size={20} />
        </Button>
      </div>

      <div className="card-nav">
        <Button variant="outline" onClick={() => markKnown(false)}>
          <RotateCcw size={15} />
          Still learning
        </Button>
        <Button onClick={() => markKnown(true)}>
          <Check size={16} />
          I know this
        </Button>
      </div>

      <div className="card-counter mt-1">
        {knownCount} of {content.cards.length} mastered
      </div>
    </div>
  );
}
