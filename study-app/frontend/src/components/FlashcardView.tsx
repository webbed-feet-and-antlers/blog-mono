import { useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Check,
  RotateCcw,
} from "lucide-react";
import { Sparkles } from "lucide-react";
import type { FlashcardContent } from "../types";

interface Props {
  content: FlashcardContent;
}

export function FlashcardView({ content }: Props) {
  const isProactive = (content as any).origin === "proactive";
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [known, setKnown] = useState<Set<string>>(new Set());

  const card = content.cards[index];
  const isLast = index === content.cards.length - 1;
  const progressPct = Math.round((known.size / content.cards.length) * 100);

  function next() {
    setFlipped(false);
    setIndex((i) => Math.min(i + 1, content.cards.length - 1));
  }
  function prev() {
    setFlipped(false);
    setIndex((i) => Math.max(i - 1, 0));
  }
  function markKnown(value: boolean) {
    setKnown((prev) => {
      const updated = new Set(prev);
      if (value) updated.add(card.id);
      else updated.delete(card.id);
      return updated;
    });
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
        {known.size} of {content.cards.length} mastered
      </div>
    </div>
  );
}
