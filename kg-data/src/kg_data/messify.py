"""Stage 2b — seeded, offset-safe noise channel.

LLM-written Slack is suspiciously clean. This pass corrupts text to match
statistics measured from real chat corpora (grounding.py): adjacent-char
transpositions, dropped punctuation, chat abbreviations, lowercase, emoji,
filler tokens. Protected spans (gold entity mentions) are never touched, and
their char offsets are re-computed for the corrupted text, so annotation
stays valid. Everything is seeded — same input, same output.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

Span = tuple[int, int]

ABBREVIATIONS = {
    "please": "pls", "thanks": "thx", "because": "cuz", "with": "w/",
    "without": "w/o", "people": "ppl", "probably": "prob",
    "tomorrow": "tmr", "minutes": "mins", "information": "info",
    "application": "app", "repository": "repo", "environment": "env",
    "production": "prod", "development": "dev", "staging": "stg",
}

SLACK_EMOJI = ["🔥", "😅", "👍", "🙏", "💀", "😂", "🤝", "👀"]
FILLERS = ["honestly", "tbh", "ngl", "fwiw"]

# rough per-channel corruption probability per eligible word
INTENSITY = {
    "slack": 0.18,
    "email": 0.03,
    "meeting_notes": 0.05,
    "confluence": 0.02,
    "drive": 0.02,
}


@dataclass
class MessifyResult:
    text: str
    spans: list[Span]  # input spans, re-mapped to the corrupted text


def _split_spans(text: str, spans: list[Span]) -> list[tuple[str, bool]]:
    """Split text into (chunk, is_protected) pieces covering it exactly."""
    pieces: list[tuple[str, bool]] = []
    cursor = 0
    for start, end in sorted(spans):
        if start < cursor or start > len(text) or end > len(text):
            continue  # ignore malformed spans
        if start > cursor:
            pieces.append((text[cursor:start], False))
        pieces.append((text[start:end], True))
        cursor = end
    if cursor < len(text):
        pieces.append((text[cursor:], False))
    return pieces


def _corrupt_word(word: str, rng: random.Random) -> str:
    if word.lower() in ABBREVIATIONS and len(word) > 3:
        abbr = ABBREVIATIONS[word.lower()]
        return abbr if word.islower() else abbr.capitalize()
    if len(word) >= 4 and word.isalpha() and rng.random() < 0.5:
        i = rng.randrange(1, len(word) - 2)
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return word


def messify(text: str, protected: list[Span], rng: random.Random,
            channel: str = "slack", intensity: float | None = None) -> MessifyResult:
    """Corrupt unprotected regions of `text`; keep `protected` spans intact.

    Returns the new text plus the protected spans' offsets in it. Ops are
    word-local (no insertions/deletions of whole words), so mentions stay
    verifiable and meaning is preserved.
    """
    p = INTENSITY.get(channel, 0.05) if intensity is None else intensity
    out: list[str] = []
    new_spans: list[Span] = []
    for chunk, is_protected in _split_spans(text, protected):
        if is_protected:
            new_spans.append((sum(len(c) for c in out), 0))  # placeholder, fixed below
            out.append(chunk)
            new_spans[-1] = (new_spans[-1][0], new_spans[-1][0] + len(chunk))
            continue
        words = chunk.split(" ")
        rebuilt: list[str] = []
        for w in words:
            if w and rng.random() < p:
                w = _corrupt_word(w, rng)
                if channel == "slack" and rng.random() < 0.15:
                    w = w.lower()
            rebuilt.append(w)
            if (channel == "slack" and rebuilt and rng.random() < p * 0.12
                    and rebuilt[-1]):
                rebuilt.append(rng.choice(SLACK_EMOJI))
        chunk = " ".join(rebuilt)
        # cosmetic: occasionally drop terminal punctuation outside mentions
        if chunk.endswith(".") and rng.random() < p:
            chunk = chunk[:-1]
        if channel == "slack" and rng.random() < p * 0.15:
            chunk = chunk.rstrip() + " " + rng.choice(FILLERS)
        out.append(chunk)
    return MessifyResult(text="".join(out), spans=new_spans)
