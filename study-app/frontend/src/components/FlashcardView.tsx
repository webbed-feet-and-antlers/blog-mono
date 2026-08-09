import { useState } from "react";
import type { FlashcardContent } from "../types";

interface Props {
  content: FlashcardContent;
}

export function FlashcardView({ content }: Props) {
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [known, setKnown] = useState<Set<string>>(new Set());

  const card = content.cards[index];
  const isLast = index === content.cards.length - 1;

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
      const next = new Set(prev);
      if (value) next.add(card.id);
      else next.delete(card.id);
      return next;
    });
    if (!isLast) next();
  }

  return (
    <div>
      <div className="card-deck">
        <div
          className="flashcard"
          onClick={() => setFlipped((f) => !f)}
          key={card.id + String(flipped)}
        >
          <span className="face-label">
            {flipped ? "Answer" : "Question"} · click to flip
          </span>
          {flipped ? card.back : card.front}
        </div>

        <div className="card-nav">
          <button onClick={prev} disabled={index === 0}>
            ← Prev
          </button>
          <span className="card-counter">
            {index + 1} / {content.cards.length}
          </span>
          <button onClick={next} disabled={isLast}>
            Next →
          </button>
        </div>

        <div className="card-nav" style={{ marginTop: 8 }}>
          <button onClick={() => markKnown(false)}>Still learning</button>
          <button className="primary" onClick={() => markKnown(true)}>
            ✓ I know this
          </button>
        </div>

        <div className="card-counter" style={{ marginTop: 4 }}>
          {known.size} / {content.cards.length} marked known
        </div>
      </div>
    </div>
  );
}
