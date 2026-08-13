# Study App — Architecture & Data Flow Documentation

> A comprehensive guide to the agent-driven study app's architecture, data flows,
> event triggers, and system interactions. Use this as a reference for understanding
> how data moves through the system and what triggers what.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Agent Pipeline](#2-agent-pipeline)
3. [Memory System](#3-memory-system)
4. [Event-Trigger Map](#4-event-trigger-map)
5. [Concept Mastery & FSRS](#5-concept-mastery--fsrs)
6. [Concept Knowledge Graph](#6-concept-knowledge-graph)
7. [Proactive Agent](#7-proactive-agent)
8. [Recommendation Engine](#8-recommendation-engine)
9. [Learner Profile](#9-learner-profile)
10. [Complete Flow Diagrams](#10-complete-flow-diagrams)
11. [Background Tasks](#11-background-tasks)
12. [Frontend Architecture](#12-frontend-architecture)

---

## 1. System Overview

The app is a single FastAPI backend + React SPA frontend. The core thesis: **the
agent is the product's engine, not a chatbot interface**. Every feature (notes,
quizzes, flashcards, recommendations) flows through one shared agent with a
memory that compounds.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         BROWSER (React SPA)                             │
│                                                                         │
│  TanStack Router          TanStack Query          MediaRecorder API     │
│  / → home/recommend       polls & invalidates     audio recording       │
│  /documents/$id/$tab      manages server state    file upload (XHR)     │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ /api (Vite proxy → :8000)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND (:8000)                            │
│                                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ Documents   │  │ Generate     │  │ Quiz         │  │ Flashcards  │  │
│  │ (upload,    │  │ (SSE stream) │  │ (submit)     │  │ (review)    │  │
│  │  file serve)│  │              │  │              │  │             │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘  │
│         │                │                 │                 │          │
│         ▼                ▼                 ▼                 ▼          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    AGENT CORE (LangGraph)                        │   │
│  │  analyze → plan → retrieve_memory → generate → validate → finalize│  │
│  └──────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                       │
│         ┌───────────────────────┼───────────────────────┐               │
│         ▼                       ▼                       ▼               │
│  ┌─────────────┐    ┌──────────────────┐    ┌────────────────────┐     │
│  │ Agent Memory │    │ Recommendation   │    │ Background Tasks   │     │
│  │ (JSON KV)    │    │ Engine (strategies)│   │ (proactive loop)   │     │
│  └──────┬──────┘    └──────────────────┘    └────────────────────┘     │
│         │                                                              │
│         ▼                                                              │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    SQLite (SQLAlchemy async)                      │  │
│  │  documents · content_items · quiz_attempts · agent_memory ·      │  │
│  │  modules · lessons · recommendation_events                       │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  External: OpenRouter (LLM) · OpenAI Whisper (transcription)          │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Agent Pipeline

The shared LangGraph `StateGraph` — all three content features (notes, quizzes,
flashcards) flow through this one pipeline.

### Node Flow

```
START
  │
  ▼
analyze_document ──────── LLM extracts: topic, concepts, difficulty,
  │                       concept_relationships (prerequisites, related).
  │                       Result cached in doc-scoped memory (runs once per doc).
  │
  ▼
plan ──────────────────── LLM decides: what to generate, how many items,
  │                       which concepts to cover, difficulty mix.
  │                       Consumes: analysis + memory (mastery, profile, FSRS).
  │
  ▼
retrieve_memory ───────── Gathers ALL context for generation:
  │                       concept_mastery (with FSRS due status + prerequisites)
  │                       learner_profile (level, difficulty, formats)
  │                       weak_topics, prior_generations, quiz_attempts
  │
  ▼
generate ──────────────── LLM creates content (notes markdown / quiz questions /
  │                       flashcard cards). Each item tagged with its `concept`.
  │                       Consumes: plan + document text + memory hints.
  │
  ▼
validate ──────────────── Structural check (no LLM):
  │                       quiz has 4 options + valid answer_idx?
  │                       cards have front + back? notes long enough?
  │                       ┌──── ok=true ────┐
  │                       │                  │
  │                  ok=false            ok=true
  │                       │                  │
  │                       ▼                  ▼
  │                      END             finalize
  │                                    (skip persist)    │
  │                                                      │
  │                                    Persists ContentItem to DB,
  │                                    updates prior_generations memory
  │                                                      │
  │                                                      ▼
  └──────────────────────────────────────────────────── END
```

### What Triggers the Pipeline

| Trigger | Entry Point | Streaming? |
|---------|-------------|------------|
| User clicks "Generate" | `POST /api/generate/stream` → `run_generation_streamed()` | Yes (SSE status events) |
| Proactive agent | `run_generation()` inside `proactive.py` | No (background) |
| Non-stream generate | `POST /api/generate` → `run_generation()` | No |

### AgentState Shape

```
AgentState = {
    # Inputs (set by caller)
    document_id: str
    document_text: str
    task_type: "notes" | "quiz" | "flashcards"
    instructions: str | None    # user hint
    session: AsyncSession       # DB session (passed through)

    # Filled by nodes
    analysis: {topic, difficulty, concepts[], concept_relationships[], ...}
    plan: {question_count, concepts_to_cover, difficulty_mix, ...}
    memory: {concept_mastery[], learner_profile{}, weak_topics[], ...}
    output: {markdown} | {title, questions[]} | {title, cards[]}
    validation: {ok: bool, problems: []}
    content_item: {id, document_id, type, content}

    # Diagnostics
    messages: [str]     # trace log
    error: str | None
}
```

---

## 3. Memory System

All agent learning state lives in one table: `agent_memory`. It's a generic
JSON key/value store with `(scope, ref_id, key)` addressing.

### Memory Key Reference

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         agent_memory table                                │
│                                                                          │
│  scope="doc" (per-document)          scope="user" (global, cross-doc)   │
│  ref_id=document_id                  ref_id=""                           │
│  ┌────────────────────────────┐      ┌─────────────────────────────┐    │
│  │                            │      │                             │    │
│  │ analysis                   │      │ concept_mastery             │    │
│  │   {topic, concepts[],      │      │   {concept: {correct,       │    │
│  │    difficulty, summary,    │      │     wrong, seen, mastery,   │    │
│  │    concept_relationships[] │      │     fsrs: {stability, due,  │    │
│  │    sections[]}             │      │       difficulty, ...},     │    │
│  │                            │      │     prerequisites[],        │    │
│  │ prior_generations          │      │     related[], documents[], │    │
│  │   [{type, content_id}]     │      │     modules[]}}             │    │
│  │                            │      │                             │    │
│  │ quiz_attempts              │      │ weak_topics                 │    │
│  │   int (count, gated)       │      │   [{topic, missed_count,    │    │
│  │                            │      │     last_seen}]             │    │
│  │                            │      │                             │    │
│  │                            │      │ learner_profile             │    │
│  │                            │      │   {level, difficulty,       │    │
│  │                            │      │    formats, goal, stats}    │    │
│  │                            │      │                             │    │
│  │                            │      │ session                     │    │
│  │                            │      │   {actions[], dismissed[]}  │    │
│  │                            │      │                             │    │
│  │                            │      │ bandit_weights              │    │
│  │                            │      │   {strategy: {A, b, W}}     │    │
│  └────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Who Writes What

| Key | Scope | Written by... | When? |
|-----|-------|---------------|-------|
| `analysis` | doc | `analyze_document` node; `analyze_concepts_background` (bg) | First generation OR upload (bg task) |
| `prior_generations` | doc | `finalize` node | Each successful generation |
| `quiz_attempts` | doc | `quiz.py` submit handler | Quiz score < threshold (gated) |
| `concept_mastery` | user | `update_concept_mastery`; `merge_concept_graph` | Every quiz answer, flashcard review, doc upload |
| `weak_topics` | user | `add_weak_topics` ← `quiz.py` | Quiz score < threshold (gated) |
| `learner_profile` | user | `update_learner_profile` | Quiz submit, flashcard review, generate hint |
| `session` | user | `record_action`; `record_dismissal` | Quiz, flashcard, or review complete |
| `bandit_weights` | user | `LinUCBOptimizer.update_weights` | Proactive loop (every 30min) |

### Who Reads What

| Key | Read by... | For what? |
|-----|-----------|-----------|
| `analysis` | `analyze_document` (cache), `retrieve_memory`, `quiz.py`, `recommend/context.py` | Doc structure, concepts, difficulty |
| `prior_generations` | `retrieve_memory` | What content already exists |
| `quiz_attempts` | `retrieve_memory` | Difficulty calibration hint |
| `concept_mastery` | `retrieve_memory`, `get_due_concepts`, `recommend/context.py`, `/api/concepts` | The core skill model |
| `weak_topics` | `retrieve_memory`, `get_review_candidates` | Proactive review triggers |
| `learner_profile` | `retrieve_memory`, `recommend/context.py`, `/api/memory/profile` | Personalization |
| `session` | `recommend/context._get_session` | Fatigue, action chaining, dismissals |
| `bandit_weights` | `recommend/bandit.get_weights` | ML-optimized strategy scoring |

---

## 4. Event-Trigger Map

The complete map of what user/system action triggers what processing.

```
USER ACTION                  TRIGGERS                              MEMORY WRITES
─────────────────────────────────────────────────────────────────────────────────────────

Upload text/PDF         →    extract_text (sync)                   concept_mastery (bg)
                             analyze_concepts_background (bg)      [via merge_concept_graph]

Upload audio            →    transcribe_then_analyze (bg)          concept_mastery (bg)
                             → Whisper transcription               [via merge_concept_graph]
                             → analyze_concepts_background (bg)

Click "Generate"        →    SSE stream → run_generation_streamed  analysis (if cache miss)
                             → analyze → plan → retrieve →         prior_generations
                               generate → validate → finalize      ContentItem (SQL table)

Submit quiz answers     →    score quiz                            concept_mastery (+ FSRS)
                             update_concept_mastery (per Q)        learner_profile
                             update_learner_profile                session
                             record_action                         weak_topics (if gated)
                             (add_weak_topics if gated)            quiz_attempts (if gated)
                                                                   QuizAttempt (SQL table)

Submit flashcard review →    update_concept_mastery (per card)     concept_mastery (+ FSRS)
                             update_learner_profile                learner_profile
                             record_action                         session

Open home screen        →    GET /api/recommend                    RecommendationEvent (SQL)
                             → build_context                       [impression logged]
                             → engine.decide()

Click recommendation    →    POST /api/recommend/feedback          RecommendationEvent (SQL)
                             → log_interaction                     [reward computed]

Dismiss recommendation  →    POST /api/recommend/feedback          RecommendationEvent (SQL)
                             → log_interaction

Proactive loop (30min)  →    run_proactive_review                  ContentItem (origin=proactive)
                             → get_review_candidates               prior_generations
                             → run_generation (flashcards)         bandit_weights
                             run_bandit_update
```

---

## 5. Concept Mastery & FSRS

`concept_mastery` is the **convergence point** of the entire system — written by
quiz, flashcard, and concept-graph flows; read by generation, recommendations,
and the concepts API.

### Data Structure

```python
concept_mastery = {
    "Calvin cycle": {
        "correct": 3,
        "wrong": 2,
        "seen": 5,
        "mastery_pct": 0.6,           # correct/seen ratio
        "fsrs": {
            "stability": 4.2,          # memory strength (days)
            "difficulty": 6.5,         # how hard for this learner
            "due": "2026-08-13T...",   # next review timestamp
            "last_review": "2026-08-09T...",
            "state": 2,               # Learning=1, Review=2, Relearning=3
            "step": None,
        },
        "prerequisites": ["Carbon fixation", "RuBisCO"],
        "related": ["Photosynthesis"],
        "documents": ["doc_abc"],
        "modules": ["BIO201 - Cell Biology"],
    },
    ...
}
```

### The Mastery + FSRS Update Chain

```
USER ANSWERS (quiz question or flashcard)
  │
  ▼
routes/quiz.py or routes/flashcards.py
  │  calls: update_concept_mastery(session, concept, correct=bool)
  │
  ▼
memory.update_concept_mastery()
  │
  ├── 1. Increment tally: seen++, correct++ or wrong++
  ├── 2. Recompute: mastery_pct = correct / seen
  ├── 3. Infer FSRS rating: correct → Good(3), wrong → Again(1)
  ├── 4. Load existing fsrs state from concept entry
  ├── 5. Call fsrs_scheduler.schedule_review(existing_fsrs, rating)
  │        │
  │        ▼
  │    fsrs library: Scheduler.review_card(card, rating)
  │        → updates stability, difficulty, due date
  │        → returns new state dict
  │        │
  │        ▼
  │    New FSRS state: {stability, difficulty, due, last_review, state, step}
  │
  ├── 6. Write updated concept entry back to concept_mastery
  │
  ▼
OUTPUT: concept's mastery_pct reflects the latest answer.
        concept's FSRS due date is now scheduled at the optimal interval.
        Next /api/recommend or /api/concepts call sees the new state.
```

### FSRS Rating Mapping

| User Action | FSRS Rating | Effect on scheduling |
|-------------|-------------|---------------------|
| "I know this" / quiz correct | Good (3) | Stability grows → next review further out |
| "Still learning" / quiz wrong | Again (1) | Stability drops → review again soon (minutes) |

---

## 6. Concept Knowledge Graph

### What it is

When a document is uploaded (text) or transcribed (audio), a background task
extracts not just concepts but their **relationships** — prerequisites, related
concepts, and part-of links. These merge into `concept_mastery` as additive
fields (`prerequisites`, `related`, `documents`, `modules`).

### Build Flow

```
DOCUMENT UPLOADED
  │
  ├── Text/PDF? ──→ asyncio.create_task(analyze_concepts_background)
  │
  └── Audio? ─────→ asyncio.create_task(transcribe_then_analyze)
                      │
                      ├── Whisper transcription → writes doc.text
                      │
                      └── then calls analyze_concepts_background
                                              │
                                              ▼
                    analyze_concepts_background(doc_id):
                      │
                      ├── 1. Load doc.text from DB
                      ├── 2. LLM: tools.analyze_document(doc.text)
                      │      → extracts: concepts[], concept_relationships[]
                      │      (prerequisite, related, part_of edges)
                      ├── 3. Cache analysis in doc memory (key="analysis")
                      ├── 4. merge_concept_graph(session, doc_id, analysis)
                      │      │
                      │      ▼
                      │    For each concept:
                      │      → Ensure concept_mastery entry exists
                      │      → Add doc_id to entry.documents[]
                      │      → Add module title to entry.modules[]
                      │    For each relationship:
                      │      → prerequisite → add to entry.prerequisites[]
                      │      → related/part_of → add to entry.related[]
                      │
                      └── commit
```

### Ambient Intelligence Pattern

This is the Claude Code / Hermes pattern: the agent works in the background,
accumulating knowledge while the user moves on. The user uploads, walks away,
and returns to find the concept graph already built — 18 concepts and 23
prerequisite relationships merged silently in ~25 seconds.

---

## 7. Proactive Agent

A background asyncio loop that pre-generates review material without any user
trigger. Runs every 30 minutes (configurable) when `proactive_enabled=true`.

```
APP STARTUP (main.py lifespan)
  │
  ├── if proactive_enabled:
  │     asyncio.create_task(proactive_loop())
  │
  ▼
proactive_loop:
  while True:
    sleep(proactive_interval_seconds)  # 30 min default
    │
    ├── run_proactive_review()
    │     │
    │     ├── get_review_candidates(session, cooldown_hours=24)
    │     │     │
    │     │     ▼
    │     │   For each analyzed document:
    │     │     weak = weak_topics ∩ doc_concepts
    │     │     due  = FSRS-due concepts ∩ doc_concepts
    │     │     relevant = weak ∪ due
    │     │     Skip if proactive deck generated within cooldown (24h)
    │     │     Returns candidates with relevant concepts
    │     │
    │     └── for each candidate:
    │           run_generation(task_type="flashcards",
    │             instructions="Review deck targeting: <concepts>")
    │           → full agent pipeline runs
    │           → tag ContentItem: origin="proactive"
    │
    ├── run_bandit_update()
    │     → loads recommendation telemetry (clicked events with rewards)
    │     → ridge-regression weight update per strategy
    │     → writes bandit_weights to agent_memory
    │
    └── (loop continues)
```

### Config

| Setting | Default | Effect |
|---------|---------|--------|
| `proactive_enabled` | `false` | Master switch |
| `proactive_interval_seconds` | `1800` (30 min) | How often the loop runs |
| `proactive_score_threshold` | `0.7` | Quiz score below this = "struggled" → weak_topics |
| `proactive_cooldown_hours` | `24` | Don't regenerate a deck for the same doc within this window |

---

## 8. Recommendation Engine

A plugin-based strategy engine that recommends what to study next. Each tool
self-describes its priority; the engine iterates strategies and scores them.

### Decision Flow

```
GET /api/recommend
  │
  ▼
build_context(session) → composes UserContext from all signals:
  │  ├── due_concepts (FSRS-due across all docs)
  │  ├── due_cards (existing flashcard cards on due concepts)
  │  ├── concept_mastery (full skill model)
  │  ├── weak_topics
  │  ├── learner_profile (level, difficulty, formats, goal)
  │  ├── documents + content coverage (notes/quiz/flashcards per doc)
  │  ├── analyses (concepts per doc)
  │  ├── proactive_decks (unseen agent-generated review decks)
  │  ├── session (recent actions, fatigue level, dismissed tools)
  │  └── computed: due_count, mastered_count, welcome_back
  │
  ▼
engine.decide(ctx):
  │
  ├── 1. EVALUATE all strategies (9 registered):
  │     for each strategy:
  │       result = strategy.evaluate(ctx) → score 0.0-1.0 or None
  │
  ├── 2. FATIGUE PENALTY (if session active):
  │     fresh (<20m): no penalty
  │     focused (20-50m): -0.05 from practice tools
  │     fatigued (50m+): -0.20 from practice tools
  │
  ├── 3. DISMISSAL PENALTY:
  │     if user dismissed a strategy this session → score × 0.1
  │
  ├── 4. EPSILON-GREEDY EXPLORATION (10% chance):
  │     boost a random non-top result by +0.5 (for bandit telemetry)
  │
  ├── 5. SORT by score descending
  │
  ├── 6. PRIMARY = top result
  │     ALTERNATIVES = complementary categories (avoid same category as primary)
  │
  └── return {primary, alternatives, context, impression_id}
```

### Strategy Scoring Table

| Strategy | Base | Condition | Boosts |
|----------|------|-----------|--------|
| Onboarding | 1.0 | No documents | — |
| DueReviewReady | 0.95 | Due concepts + existing cards | Soft override (FSRS urgency) |
| DueReviewGenerate | 0.85 | Due concepts, no cards | — |
| ProactiveDeck | 0.80 | Unseen proactive deck | — |
| QuizGap | 0.60 | Notes exist, no quiz | — |
| StartNotes | 0.55 | Doc with no content | — |
| Quiz | 0.30 | Documents exist | +0.40 if last action was flashcards, +0.10 momentum |
| Flashcard | 0.25 | Documents exist | +min(due_count/20, 0.30) FSRS urgency |
| Fallback | 0.10 | Always | — |

### Telemetry → ML Loop

```
IMPRESSION                      INTERACTION                    BANDIT UPDATE
(GET /api/recommend)            (user clicks/dismisses)         (proactive loop, 30min)
                                │
  → log RecommendationEvent     → POST /api/recommend/feedback  → load clicked events
    (event_type="impression")     → calculate_reward()            with rewards
                                  → log RecommendationEvent       → ridge regression
    [one per shown rec]             (event_type=action)            per strategy
                                                                  → update A, b, W
REWARD VALUES:                                                  → clamp [-1.0, 2.0]
  completed = +1.0                                              → write bandit_weights
  clicked   = +0.4                                              → next decide() reads
  dismissed = -0.5                                                 updated weights
  abandoned = -0.1
```

---

## 9. Learner Profile

Automatically inferred from behavior — no forms, no onboarding. Four dimensions:

| Dimension | Values | How Inferred |
|-----------|--------|-------------|
| **learner_level** | beginner / intermediate / advanced | Difficulty-weighted avg quiz score (last 10). Acing hard docs → advanced. |
| **preferred_difficulty** | easy / medium / hard | Drifts from level: last 5 scores >80% → bump up, <50% → ease off |
| **preferred_formats** | quiz_length, card_style, notes_depth | From hint input (persisted): "10 questions" → quiz_length=10, "concise" → notes_depth=concise |
| **study_goal** | exam_prep / casual / skill_building | Cadence: 3+ quizzes within 6h → exam_prep. Hint keywords. |

All inference is **deterministic** (no LLM) — pure math, thresholds, and regex.
Updated on every quiz submit, flashcard review, and generate hint.

### How the Profile Flows Into Generation

```
retrieve_memory node
  │
  ├── reads learner_profile from agent_memory
  ├── places into memory["learner_profile"]
  │
  ▼
_memory_hint (tools.py)
  │
  ├── renders: "Intermediate learner, avg 72%, prefers medium difficulty,
  │            8-question quizzes, studying for exam prep."
  │
  ▼
plan_task system prompt
  │
  ├── "Use the learner_profile to calibrate: match preferred_difficulty
  │   for the difficulty mix. Use preferred_formats as defaults."
  │
  ▼
Generated content is personalized (difficulty, format, depth)
```

---

## 10. Complete Flow Diagrams

### Quiz Submission — Full Chain

```
USER SUBMITS QUIZ ANSWERS
  │
  ▼
POST /api/quiz/{content_id}/attempt
  │
  ├── 1. SCORE: for each question
  │      is_correct = answers[q.id] == q.answer_idx
  │
  ├── 2. MASTERY (always-on, per question):
  │      update_concept_mastery(concept, correct=is_correct)
  │      → tally update + FSRS scheduling
  │
  ├── 3. WEAK TOPICS (gated: proactive_enabled AND score < 0.7):
  │      match missed concepts → add_weak_topics()
  │      increment quiz_attempts counter
  │
  ├── 4. PROFILE (always-on):
  │      update_learner_profile(quiz_score, doc_difficulty)
  │      → recompute level, drift difficulty, update stats
  │
  ├── 5. SESSION:
  │      record_action("quiz", doc_id)
  │      → for action chaining + fatigue
  │
  ├── 6. PERSIST: QuizAttempt row (score, answers, timestamp)
  │
  └── COMMIT all writes atomically

  Memory writes:
    concept_mastery (user)  — per-question mastery + FSRS
    weak_topics (user)      — if gated open
    quiz_attempts (doc)     — if gated open
    learner_profile (user)  — always
    session (user)          — always
  SQL writes:
    quiz_attempts table     — always
```

### Document Upload — Text vs Audio

```
UPLOAD REQUEST (POST /api/documents)
  │
  ├── Is it audio? (.webm/.mp3/.m4a/.wav/.ogg)
  │   │
  │   ├── Save file to storage/
  │   ├── Create Document(kind="audio", text="", transcription_status="pending")
  │   ├── Commit + return 201 immediately
  │   │
  │   └── BG TASK: transcribe_then_analyze(doc_id)
  │       │
  │       ├── Set status="transcribing"
  │       ├── Whisper API transcription (chunk if >25MB)
  │       ├── Write transcript to doc.text
  │       ├── Set status="done"
  │       └── Call analyze_concepts_background(doc_id)
  │           → builds concept graph from transcript
  │
  └── Is it text/PDF/MD?
      │
      ├── Save file to storage/
      ├── extract_text (PyMuPDF for PDF, read_text for txt/md)
      ├── Create Document(kind="text", text=extracted_text)
      ├── Commit + return 201
      │
      └── BG TASK: analyze_concepts_background(doc_id)
          → builds concept graph from extracted text
```

### Generate (SSE Streaming) — Full Chain

```
USER CLICKS "GENERATE"
  │
  ▼
POST /api/generate/stream {document_id, task_type, instructions}
  │
  ├── If instructions present: persist as profile hint (learner_profile)
  │
  ▼
SSE STREAM (text/event-stream):
  │
  ├── event: status  "Reading the document…"     ← analyze_document node
  ├── event: status  "Planning what to create…"   ← plan node
  ├── event: status  "Recalling what you know…"   ← retrieve_memory node
  ├── event: status  "Creating your quiz…"        ← generate node
  ├── event: status  "Checking the quality…"      ← validate node
  ├── event: status  "Saving the results…"        ← finalize node
  │
  └── event: done    {item: ContentItem}
      │
      ▼
  Frontend invalidates ["recommend"] → re-fetches recommendations
  Content appears in the tab
```

---

## 11. Background Tasks

Three background tasks in the system:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. PROACTIVE LOOP (long-running, app lifetime)                          │
│    Spawn: main.py lifespan → asyncio.create_task(proactive_loop())      │
│    Cancel: main.py lifespan teardown                                    │
│    Schedule: every proactive_interval_seconds (30 min default)         │
│    Does: run_proactive_review() + run_bandit_update()                   │
│    Guard: only runs if proactive_enabled=true                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 2. CONCEPT ANALYSIS (one-shot, per text/PDF upload)                     │
│    Spawn: routes/documents.py → asyncio.create_task(...)                │
│    Trigger: successful text/PDF upload commit                           │
│    Does: LLM analysis → cache analysis → merge_concept_graph            │
│    Lifetime: runs once, completes in ~20-30s                           │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 3. TRANSCRIPTION + ANALYSIS (one-shot, per audio upload)                │
│    Spawn: routes/documents.py → asyncio.create_task(...)                │
│    Trigger: successful audio upload commit                              │
│    Does: Whisper transcription → write transcript → call task #2        │
│    Lifetime: runs once, completes in seconds to minutes                 │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 12. Frontend Architecture

### Route Structure (TanStack Router, code-based)

```
/                                    → RecommendationPanel (home)
  │                                     └── polls /api/recommend every 15s
  │
  ├── /documents/$docId               → redirect to /documents/$docId/document
  │
  └── /documents/$docId/$tab          → DocTabView
        │
        ├── tab=document               → DocumentView (source text or audio player)
        ├── tab=notes                  → NotesView (markdown render) + Generate bar
        ├── tab=quiz                   → QuizView (interactive MCQ) + Generate bar
        ├── tab=flashcards             → FlashcardView (3D flip cards) + Generate bar
        └── tab=concepts               → ConceptListView (knowledge graph + mastery)
```

### Layout

```
┌──────────────┬──────────────────────────────────────────────┐
│              │                                              │
│  SIDEBAR     │              MAIN CONTENT                     │
│              │                                              │
│  ┌────────┐  │  ┌──────────────────────────────────────┐   │
│  │ 📚 Logo│  │  │                                      │   │
│  └────────┘  │  │  [Document tab] [Notes] [Quiz] ...  │   │
│              │  │                                      │   │
│  Module tree │  │  Content area:                      │   │
│  ├ Module    │  │  - RecommendationPanel (home)       │   │
│  │ ├ Lesson  │  │  - DocumentView / NotesView /       │   │
│  │ │ └ Doc   │  │    QuizView / FlashcardView /       │   │
│  │ └ Lesson  │  │    ConceptListView                  │   │
│  └ Unfiled   │  │                                      │   │
│              │  └──────────────────────────────────────┘   │
│  ProfileCard │                                              │
│              │                                              │
│  Upload zone │                                              │
│  Record btn  │                                              │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

### Key Frontend Patterns

- **TanStack Query** for all server state — polls, invalidation, optimistic updates
- **XHR upload** with `onprogress` for large audio files
- **MediaRecorder API** for in-browser audio recording
- **SSE via fetch + ReadableStream** for generation status streaming
- **localStorage** for sidebar tree expansion state + proactive deck dismissal
- **Module-level `pendingGenerate` flag** for cross-component generation trigger (recommendation panel → DocTabView)

---

## Appendix: File Map

```
study-app/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, lifespan, router registration
│   │   ├── config.py               # Settings (OpenRouter, Whisper, proactive)
│   │   ├── db.py                   # SQLAlchemy async engine + migrations
│   │   ├── models.py               # Module, Lesson, Document, ContentItem,
│   │   │                           #   QuizAttempt, AgentMemory, RecommendationEvent
│   │   ├── schemas.py              # Pydantic request/response models
│   │   ├── storage.py              # Filesystem layer for uploads
│   │   ├── parsers.py              # PyMuPDF text extraction
│   │   ├── transcription.py        # Whisper transcription + background orchestrator
│   │   ├── proactive.py            # Background loop: review generation + bandit
│   │   ├── llm.py                  # OpenRouter chat client with retry
│   │   ├── agent/
│   │   │   ├── graph.py            # LangGraph StateGraph + streaming
│   │   │   ├── state.py            # AgentState TypedDict
│   │   │   ├── nodes.py            # 6 pipeline nodes
│   │   │   ├── tools.py            # Generation tools + _memory_hint
│   │   │   ├── memory.py           # All memory helpers (mastery, FSRS, profile)
│   │   │   ├── fsrs_scheduler.py   # FSRS wrapper (dict adapter)
│   │   │   └── concept_graph.py    # Background analysis + graph merge
│   │   ├── recommend/
│   │   │   ├── context.py          # UserContext builder
│   │   │   ├── engine.py           # Strategy registry + decision loop
│   │   │   ├── session.py          # Session tracking (actions, fatigue)
│   │   │   ├── telemetry.py        # Impression/interaction logging
│   │   │   ├── bandit.py           # LinUCB contextual bandit
│   │   │   └── strategies/         # 9 strategy implementations
│   │   └── routes/
│   │       ├── documents.py        # Upload, list, get, delete, file serve
│   │       ├── generate.py         # Generate + SSE stream
│   │       ├── content.py          # Content CRUD
│   │       ├── quiz.py             # Quiz submit + scoring
│   │       ├── flashcards.py       # Flashcard review
│   │       ├── modules.py          # Module/Lesson CRUD + tree
│   │       ├── concepts.py         # Concept list (knowledge graph API)
│   │       ├── memory.py           # Memory/proactive/profile debug endpoints
│   │       └── recommend.py        # Recommendation + feedback
│   └── pyproject.toml
│
├── frontend/
│   └── src/
│       ├── main.tsx                # App mount (RouterProvider)
│       ├── router.tsx              # TanStack Router route tree
│       ├── types.ts                # Shared TS types
│       ├── api/client.ts           # Typed fetch wrapper + all API functions
│       ├── styles/global.css       # Full stylesheet (light theme)
│       └── components/
│           ├── Sidebar.tsx         # Module tree + upload + record + profile
│           ├── DocTabView.tsx      # Document view with tabs
│           ├── DocumentView.tsx    # Source text / audio player / transcript
│           ├── NotesView.tsx       # Markdown render
│           ├── QuizView.tsx        # Interactive MCQ with scoring
│           ├── FlashcardView.tsx   # 3D flip cards with review tracking
│           ├── ConceptListView.tsx # Knowledge graph + mastery display
│           ├── RecommendationPanel.tsx # Home screen recommendations
│           └── ProfileCard.tsx     # Learner profile badge
│
└── README.md
```
