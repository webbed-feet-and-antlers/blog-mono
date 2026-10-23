"""Stage 0 — grounding in real corpora.

LLM-written Slack is too clean. This stage pulls small capped samples of
REAL messy communication (Ubuntu IRC dialogues, Enron emails, Stack Exchange
posts) via HF streaming, then derives:

  - exemplars.json  per-channel style snippets used as few-shots in docgen
  - noise_stats.json per-channel measured messiness (lengths, abbreviation /
    lowercase / emoji rates) for tuning messify intensity
  - jargon.json     real infra/dev vocabulary for worldgen naming texture

Schemas of community HF mirrors drift, so every parser introspects candidate
field names and skips corpora it cannot understand — grounding enhances
realism but is never load-bearing for the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from pathlib import Path

from .config import DATA_DIR, settings

logger = logging.getLogger(__name__)

GROUNDING_DIR = DATA_DIR / "grounding"

CORPORA = {
    "ubuntu": {"repo": "ntcuong777/ubuntu_dialogue_corpus_train", "cap": 1500,
               "split": "train", "fields": ["Context", "Utterance"]},
    "enron": {"repo": "corbt/enron-emails", "cap": 800,
              "split": "train", "fields": ["body", "text"]},
    "stackexchange": {"repo": "HuggingFaceGECLM/StackExchange_Mar2023", "cap": 1200,
                      "split": "stackoverflow", "fields": ["text"]},
}

CHANNEL_SOURCES = {
    "slack": ["ubuntu"],
    "email": ["enron"],
    "confluence": ["stackexchange"],
    "drive": ["stackexchange"],
    "meeting_notes": ["ubuntu"],
}

ABBREV_TOKENS = {"pls", "thx", "cuz", "w/", "w/o", "ppl", "prob", "tmr",
                 "u", "ur", "imho", "imo", "afk", "brb", "lol", "tbh", "ngl",
                 "fwiw", " prolly", "y'all", "gonna", "wanna", "kinda"}

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF]"
)

STOPWORDS = set("""the a an and or but if then else for to of in on at by with from
as is are was were be been being this that these those it its i you he she we they
them his her their our your my not no yes do does did have has had will would can
could should may might must about into over under out up down again there here when
where why how all any both each few more most other some such only own same so than
too very just also now what which who whom while during before after above below
because until against between through don should've aren didn couldn hasn hasn't
isn'mightn mustn needn shan shouldn wasn weren won wouldn't get got like want know
really think said say says one two thing things time way lot bit""".split())


def _first_field(row: dict, fields: list[str]) -> str | None:
    for f in fields:
        v = row.get(f)
        if isinstance(v, str) and len(v.strip()) > 20:
            return v
        if isinstance(v, list) and v and isinstance(v[0], str):
            return "\n".join(str(x) for x in v[-8:])
    return None


def _iter_corpus(repo: str, split: str, cap: int):
    from datasets import load_dataset

    ds = load_dataset(repo, split=split, streaming=True)
    rows = []
    for row in ds:
        rows.append(dict(row))
        if len(rows) >= cap:
            break
    return rows


def _clean_snippet(text: str, max_chars: int = 600) -> str:
    text = text.replace("__eou__", "\n").replace("__eot__", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        text = cut[: cut.rfind("\n") if "\n" in cut else len(cut)]
    return text.strip()


def _extract_texts(rows: list[dict], fields: list[str]) -> list[str]:
    out = []
    for row in rows:
        # corpus-specific combination before generic field picking
        if "Context" in row and "Utterance" in row:  # Ubuntu Dialogue
            t = f"{row['Context']} __eou__ {row['Utterance']}"
        elif row.get("subject") and row.get("body"):  # Enron
            t = f"Subject: {row['subject']}\n\n{row['body']}"
        else:
            t = _first_field(row, fields) or ""
        t = t.strip()
        if len(t) > 25:
            out.append(t)
    return out


def _sentences(texts: list[str]) -> list[str]:
    sents = []
    for t in texts:
        for line in t.replace("\r", "").split("\n"):
            for s in re.split(r"(?<=[.!?])\s+", line):
                s = s.strip()
                if 15 <= len(s) <= 300:
                    sents.append(s)
    return sents


def measure_noise(texts: list[str]) -> dict:
    sents = _sentences(texts)
    if not sents:
        return {}
    n = len(sents)
    words = [len(s.split()) for s in sents]
    abbrev = sum(1 for s in sents
                 if any(f" {a.strip()} " in f" {s.lower()} " for a in ABBREV_TOKENS))
    lower_start = sum(1 for s in sents if s[:1].islower())
    emoji = sum(1 for s in sents if EMOJI_RE.search(s))
    return {
        "sentences": n,
        "mean_words": round(sum(words) / n, 1),
        "abbrev_rate": round(abbrev / n, 3),
        "lowercase_start_rate": round(lower_start / n, 3),
        "emoji_rate": round(emoji / n, 3),
    }


def harvest_jargon(texts: list[str], top: int = 250) -> list[str]:
    counter: Counter[str] = Counter()
    for t in texts:
        t = t.replace("__eou__", " ").replace("__eot__", " ")
        for w in re.findall(r"[a-z][a-z0-9\-_.]{2,}", t.lower()):
            if w not in STOPWORDS:
                counter[w] += 1
    return [w for w, _ in counter.most_common(top)
            if "__" not in w and w not in
            ("use", "using", "used", "new", "need", "needs", "make",
             "makes", "good", "see", "want", "wants", "know", "like",
             "way", "thing", "things", "really", "just")]


def prepare_all(caps: dict[str, int] | None = None) -> dict:
    """Download + derive everything. Returns the summary written to disk."""
    caps = caps or {}
    GROUNDING_DIR.mkdir(parents=True, exist_ok=True)
    (GROUNDING_DIR / "raw").mkdir(exist_ok=True)

    texts_by_corpus: dict[str, list[str]] = {}
    for name, spec in CORPORA.items():
        cap = caps.get(name, spec["cap"])
        try:
            rows = _iter_corpus(spec["repo"], spec["split"], cap)
            texts = _extract_texts(rows, spec["fields"])
            texts_by_corpus[name] = texts
            with (GROUNDING_DIR / "raw" / f"{name}.jsonl").open("w") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            logger.info("grounding: %s → %d usable texts", name, len(texts))
        except Exception as exc:  # schema drift / network — degrade gracefully
            logger.warning("grounding: skipping %s (%s)", name, exc)

    # per-channel exemplars: short snippets from the corpus mapped to the channel
    exemplars: dict[str, list[str]] = {}
    for channel, sources in CHANNEL_SOURCES.items():
        pool: list[str] = []
        for src in sources:
            for t in texts_by_corpus.get(src, []):
                snip = _clean_snippet(t)
                if 60 <= len(snip) <= 500:
                    pool.append(snip)
        exemplars[channel] = pool[:40]

    noise = {name: measure_noise(texts) for name, texts in texts_by_corpus.items()}
    jargon = harvest_jargon(
        texts_by_corpus.get("ubuntu", []) + texts_by_corpus.get("stackexchange", []))

    (GROUNDING_DIR / "exemplars.json").write_text(
        json.dumps(exemplars, ensure_ascii=False, indent=1))
    (GROUNDING_DIR / "noise_stats.json").write_text(
        json.dumps(noise, indent=1))
    (GROUNDING_DIR / "jargon.json").write_text(
        json.dumps(jargon, ensure_ascii=False, indent=1))

    return {"corpora": {k: len(v) for k, v in texts_by_corpus.items()},
            "exemplars": {k: len(v) for k, v in exemplars.items()},
            "noise": noise, "jargon": len(jargon)}


async def prepare_all_async() -> dict:
    """Thread-offloaded so the CLI keeps a single asyncio.run."""
    return await asyncio.to_thread(prepare_all)


def load_exemplar_for(channel: str, *, seed: int = 0) -> str | None:
    """One exemplar snippet for a channel (seeded, stable per world)."""
    path = GROUNDING_DIR / "exemplars.json"
    if not path.exists():
        return None
    exemplars = json.loads(path.read_text())
    pool = exemplars.get(channel) or []
    if not pool:
        return None
    import random

    return random.Random(seed).choice(pool)
