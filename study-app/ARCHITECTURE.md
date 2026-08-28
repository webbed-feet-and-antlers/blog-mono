# Study App — Architecture & Data Flow Documentation

> A comprehensive guide to the agent-driven study app's architecture, data flows,
> event bus, and system interactions. Use this as a reference for understanding
> how data moves through the system and what triggers what.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Agent Pipeline](#2-agent-pipeline)
3. [Memory System](#3-memory-system)
4. [Event Bus & Event-Trigger Map](#4-event-bus--event-trigger-map)
5. [Concept Mastery & FSRS](#5-concept-mastery--fsrs)
6. [Study Session Composer](#6-study-session-composer)
7. [Concept Knowledge Graph](#7-concept-knowledge-graph)
8. [Study Plans](#8-study-plans)
9. [Behavior Tracking & Reflection](#9-behavior-tracking--reflection)
10. [Proactive Agent](#10-proactive-agent)
11. [Recommendation Engine](#11-recommendation-engine)
12. [Learner Profile](#12-learner-profile)
13. [Evals](#13-evals)
14. [Complete Flow Diagrams](#14-complete-flow-diagrams)
15. [Background Tasks](#15-background-tasks)
16. [Frontend Architecture](#16-frontend-architecture)

---

## 1. System Overview

The app is a single FastAPI backend + React SPA frontend. The core thesis: **the
agent is the product's engine, not a chatbot interface**. Every feature (notes,
quizzes, flashcards, recommendations, study plans) flows through one shared
agent with a memory that compounds. Every automatic behavior rides an in-process
event bus with a full audit ledger.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         BROWSER (React SPA)                             │
│                                                                         │
│  TanStack Router          TanStack Query          MediaRecorder API     │
│  / → home/recommend       polls & invalidates     audio recording       │
│  /documents/$id/$tab      manages server state    file upload (XHR)     │
│  /record /study           track() telemetry       react-pdf viewer      │
│  /modules /concepts       (batched, sendBeacon)   SSE via fetch stream  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ /api (Vite proxy → :8000)
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      FASTAPI BACKEND (:8000)                            │
│                                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ Documents   │  │ Generate     │  │ Quiz /       │  │ Modules /   │  │
│  │ (upload,    │  │ (SSE stream) │  │ Flashcards / │  │ Plans /     │  │
│  │  file serve)│  │              │  │ StudySession │  │ Lectures    │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘  │
│         │    thin routes: core write + commit + ONE publish    │        │
│         ▼                ▼                 ▼                 ▼          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │              EVENT BUS (in-process, post-commit)                 │   │
│  │  DocumentIngested · DocumentAnalyzed · QuizAttempted ·           │   │
│  │  FlashcardsReviewed · StudySessionReviewed ·                     │   │
│  │  GenerationCompleted · ActivitiesLogged · StudyPlanStaleDetected │   │
│  │  → handlers (own transactions) → agent_events audit ledger       │   │
│  └──────────────────────────────┬───────────────────────────────────┘   │
│                                 ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    AGENT CORE (LangGraph)                        │   │
│  │  analyze → retrieve_memory → plan → generate → validate →        │   │
│  │            finalize                                              │   │
│  └──────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                       │
│     ┌───────────────┬───────────┴──────────┬────────────────────┐      │
│     ▼               ▼                      ▼                    ▼      │
│  ┌────────────┐ ┌─────────────┐ ┌────────────────────┐ ┌────────────┐  │
│  │ Agent      │ │ Recommender │ │ Study Planner      │ │ Reflection │  │
│  │ Memory     │ │ (12         │ │ (per-module,       │ │ (behavior  │  │
│  │ (JSON KV)  │ │  strategies)│ │  exam-paced)       │ │  → LLM)    │  │
│  └─────┬──────┘ └─────────────┘ └────────────────────┘ └────────────┘  │
│        │                                                                │
│        ▼                                                                │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    SQLite (SQLAlchemy async, WAL)                 │  │
│  │  documents · content_items · quiz_attempts · agent_memory ·      │  │
│  │  modules · lessons · lecture_sessions · study_plans ·            │  │
│  │  recommendation_events · agent_events · user_activities          │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  External: OpenRouter — LLM (deepseek/deepseek-v4-flash-0731) ·            │
│            eval judge (deepseek/deepseek-v4-flash-0731) ·                      │
│            transcription (qwen/qwen3-asr-1.7b)                        │
│  Local:    LibreOffice headless (office → PDF at upload)              │
└────────────────────────────────────────────────────────────────────────┘
```

Organization model: **Module** (with academic year, term, exam date) →
**Lesson** → **Document** (text or audio). A document belongs to a lesson _or_
directly to a module — never both — or to neither (unfiled). Lectures group an
audio recording + slide deck + notes + slide↔audio timestamps
(`LectureSession`).

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
retrieve_memory ───────── Gathers ALL context for generation:
  │                       concept_mastery (with FSRS due status, retrievability,
  │                         per-concept latency, prerequisites)
  │                       learner_profile (level, difficulty, formats,
  │                         flashcard_known_ratio, score trend)
  │                       learner_insights (behavioral narrative)
  │                       weak_topics, prior_generations, quiz_attempts
  │
  ▼
plan ──────────────────── LLM decides: what to generate, how many items,
  │                       which concepts to cover, difficulty mix.
  │                       Consumes: analysis + memory (mastery, profile, FSRS).
  │
  ▼
generate ──────────────── LLM creates content (notes markdown / quiz questions /
  │                       flashcard cards, 2+ variants per concept).
  │                       Each item tagged with its `concept`.
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

> **Ordering note:** `retrieve_memory` runs **before** `plan`, deliberately. The
> original graph ran `plan → retrieve_memory`, which meant the planner's
> "weight toward weak topics / personalize difficulty" instructions always
> received an empty memory dict — dead code from day one. Memory-first makes the
> planner's personalization actually fire.

### What Triggers the Pipeline

| Trigger                    | Entry Point                                               | Streaming?              | Origin tag    |
| -------------------------- | --------------------------------------------------------- | ----------------------- | ------------- |
| User clicks "Generate"     | `POST /api/generate/stream` → `run_generation_streamed()` | Yes (SSE status events) | —             |
| User generate (non-stream) | `POST /api/generate` → `run_generation()`                 | No                      | —             |
| Auto-generation on upload  | `DocumentAnalyzed` → ingestion handler (flag-gated)       | No (background)         | `"auto"`      |
| Proactive agent            | `run_generation()` inside `proactive.py`                  | No (background)         | `"proactive"` |

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
    memory: {concept_mastery[], learner_profile{}, learner_insights{},
             weak_topics[], ...}
    plan: {question_count, concepts_to_cover, difficulty_mix, ...}
    output: {markdown} | {title, questions[]} | {title, cards[]}
    validation: {ok: bool, problems: []}
    content_item: {id, document_id, type, content}

    # Diagnostics
    messages: [str]     # trace log
    error: str | None
}
```

---

### Auth & Multi-User Identity

The app is multi-user: identity comes from [Clerk](https://clerk.com), and
every piece of user data is owner-scoped by the Clerk user id.

```
BROWSER                          BACKEND
┌──────────────────┐   JWT   ┌──────────────────────────────────┐
│ ClerkProvider    │ ──────► │ get_current_user (app/auth.py)   │
│  SignIn/SignUp   │  Bearer │  ├─ verify via clerk-backend-api│
│  UserButton      │  or     │  ├─ 401 when invalid/expired    │
│  TokenBridge ────┼─?token= │  └─ set current_user_id (ctxvar)│
└──────────────────┘ (img/   └──────────┬───────────────────────┘
                        beacon)         │ every query/write resolves
                                        ▼ the owner from the contextvar
                     documents/content/modules/plans/lectures/events
                     agent_memory blobs (user scope: ref_id = user id)
                     UserActivity / QuizAttempt / RecommendationEvent
```

Key mechanics:

- **Token transport.** Clerk session JWTs (~60s TTL) ride as `Authorization:
Bearer` on fetch/XHR/SSE; URLs that cannot carry headers (slide `<img>`,
  file downloads, `sendBeacon` telemetry) append `?token=`. The frontend
  keeps the freshest token in a small store (`src/auth.ts`) fed by
  `<ClerkTokenBridge />` (50s refresh + on focus).
- **Request identity.** `app/auth.py:get_current_user` verifies the JWT
  (authorized parties = the dev origins), 401s without a valid session,
  and stores the Clerk `sub` (user id) in a `ContextVar`. The contextvar
  flows through the whole call tree, so memory helpers, agent nodes, and
  event handlers resolve the owner without threading an explicit user
  parameter through every signature. Ambient callers with no request
  context (tests, evals, CLI) resolve to the implicit default user `""` —
  the same single-user behavior those callers always had.
- **Row ownership.** Every user-specific table carries `user_id` (Clerk
  id; `""` = ambient). Lists filter by owner; cross-user ids 404
  (existence is not disclosed). `study_plans` uniqueness moved from
  `module_id` to `(user_id, module_id)` — one plan per module per user.
- **Events.** Every domain event inherits `UserEvent`, capturing the
  current owner at construction; the bus re-applies the event's user
  around each handler run (handlers use fresh sessions outside the
  request, so the request contextvar doesn't reach them).
- **Background jobs.** The proactive loop and reflection iterate the
  known user ids, running each user's pass inside `user_scope(uid)` with
  per-user error isolation.
- **Streamed generation.** The SSE generator outlives the request scope,
  so it re-establishes `current_user_id` inside the generator before any
  memory writes.
- **Tests.** `get_current_user` is overridden via
  `app.dependency_overrides` (ambient user `""`); `tests/test_auth.py`
  pins the 401 contract and two-user isolation (documents, content,
  memory blobs, concept mastery).

## 3. Memory System

All agent learning state lives in one table: `agent_memory`. It's a generic
JSON key/value store with `(scope, ref_id, key)` addressing. No User table —
this is a single-user POC; "user" is a scope.

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
│  │    concept_relationships[] │      │     latency: {avg_secs,     │    │
│  │    sections[]}             │      │       samples},             │    │
│  │                            │      │     fsrs: {stability, due,  │    │
│  │ prior_generations          │      │       difficulty, ...},     │    │
│  │   [{type, content_id}]     │      │     prerequisites[],        │    │
│  │                            │      │     related[], documents[], │    │
│  │ quiz_attempts              │      │     modules[]}}             │    │
│  │   int (count, gated)       │      │                             │    │
│  │                            │      │ weak_topics                 │    │
│  │                            │      │   [{topic, missed_count,    │    │
│  │                            │      │     last_seen}]             │    │
│  │                            │      │                             │    │
│  │                            │      │ learner_profile             │    │
│  │                            │      │   {level, difficulty,       │    │
│  │                            │      │    formats, goal,           │    │
│  │                            │      │    stats: {score_history,   │    │
│  │                            │      │      avg_score,             │    │
│  │                            │      │      flashcard_known_ratio}}│    │
│  │                            │      │                             │    │
│  │                            │      │ learner_insights            │    │
│  │                            │      │   {summary, traits[],       │    │
│  │                            │      │    habits, updated_at}      │    │
│  │                            │      │                             │    │
│  │                            │      │ engagement                  │    │
│  │                            │      │   {docs: {id: {views,       │    │
│  │                            │      │     dwell_secs,             │    │
│  │                            │      │     last_viewed}}, …}       │    │
│  │                            │      │                             │    │
│  │                            │      │ study_patterns              │    │
│  │                            │      │   {hour_histogram,          │    │
│  │                            │      │    best_study_hour,         │    │
│  │                            │      │    avg_quiz_duration_secs,  │    │
│  │                            │      │    sessions: {completed,    │    │
│  │                            │      │     abandoned}}             │    │
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

| Key                 | Scope | Written by...                                                                           | When?                                                              |
| ------------------- | ----- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `analysis`          | doc   | `events/handlers/ingestion.ingest_document` (background chain)                          | Upload / transcription completes                                   |
| `prior_generations` | doc   | `finalize` node                                                                         | Each successful generation                                         |
| `quiz_attempts`     | doc   | `study.detect_weak_topics` handler                                                      | Quiz score < threshold (gated)                                     |
| `concept_mastery`   | user  | `study.update_mastery` handler; `ingestion.merge_graph_and_retitle`                     | Every quiz answer, card review, doc analysis                       |
| `weak_topics`       | user  | `study.detect_weak_topics` handler                                                      | Quiz score < threshold (gated)                                     |
| `learner_profile`   | user  | `study.update_profile_from_quiz` / `update_profile_from_cards` handlers                 | Quiz submit, flashcard/session review, generate hint               |
| `learner_insights`  | user  | `reflection.reflect_on_learner`                                                         | Proactive loop tick or `POST /api/memory/reflect` (cooldown-gated) |
| `engagement`        | user  | `behavior.distill_activities` ← `ActivitiesLogged`                                      | Every telemetry batch flush                                        |
| `study_patterns`    | user  | `behavior.distill_activities`; `record_quiz_duration`; `record_study_session_completed` | Telemetry flush; quiz submit; session complete                     |
| `session`           | user  | `study.record_activity` handler                                                         | Quiz, flashcard, session, or generation completes                  |
| `bandit_weights`    | user  | `LinUCBOptimizer.update_weights`                                                        | Proactive loop (every 30 min default)                              |

### Who Reads What

| Key                 | Read by...                                                                                                          | For what?                                                               |
| ------------------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `analysis`          | `analyze_document` (cache), `retrieve_memory`, `recommend/context.py`, weak-topic matching                          | Doc structure, concepts, difficulty                                     |
| `prior_generations` | `retrieve_memory`                                                                                                   | What content already exists                                             |
| `quiz_attempts`     | `retrieve_memory`                                                                                                   | Difficulty calibration hint                                             |
| `concept_mastery`   | `retrieve_memory`, `get_due_concepts`, `recommend/context.py`, `/api/concepts`, planner grounding, session composer | The core skill model                                                    |
| `weak_topics`       | `retrieve_memory`, `get_review_candidates`, planner, weak-spot + proactive-deck strategies                          | Proactive review triggers                                               |
| `learner_profile`   | `retrieve_memory`, `recommend/context.py`, planner, `/api/memory/profile`                                           | Personalization                                                         |
| `learner_insights`  | `tools._memory_hint`, planner grounding, understanding panel; rides (unscored) on recommend context                 | Behavioral narrative                                                    |
| `engagement`        | revisit strategy, planner (unread docs), recommend context (neglected docs)                                         | Attention signals                                                       |
| `study_patterns`    | recommend engine (peak hour, format tilt), reflection packet                                                        | When/how the learner studies                                            |
| `session`           | `recommend/context._get_session`                                                                                    | Fatigue, action chaining, dismissals                                    |
| `bandit_weights`    | `recommend/bandit.get_weights`                                                                                      | ML-optimized strategy scoring (not yet wired into `decide()` — see §11) |

---

## 4. Event Bus & Event-Trigger Map

Every automatic behavior in the app rides an **in-process domain event bus**
(`app/events/bus.py`). No broker, no queue — an asyncio dispatcher plus the
discipline to use it. Routes do exactly two things: the user's core write
(committed) and one `publish` announcing what happened.

### Publish, Don't Call

```python
await session.commit()                  # the user's action is durable
await bus.publish(QuizAttempted(...))   # the agent's reactions run after
```

Delivery semantics:

- **Post-commit.** The core write is already durable when reactions run. A
  failing handler is logged and skipped — it can never roll back or 500 the
  user's action.
- **Inline handlers** (default) are awaited inside `publish()`, each in its own
  session with its own commit. Fast DB reactions finish before the HTTP
  response returns.
- **`background=True` handlers** are spawned as tracked asyncio tasks for slow
  work (LLM calls). Task references are held so exceptions are always logged,
  never silently dropped.
- **The ledger.** Every publish writes a dispatch row to `agent_events`; every
  handler run writes an `ok` row on the same transaction as its writes (the row
  commits iff the writes commit) or a `failed` row with the error. `GET
/api/events` exposes the whole log — the answer to "what did the agent do,
  and when?"

### Domain Events

| Event                    | Published when...                                 | Handlers                                                                                                                                                                             |
| ------------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `DocumentIngested`       | Upload committed (source="upload" \| "audio")     | `ingest.ingest_document` (bg)                                                                                                                                                        |
| `DocumentAnalyzed`       | Rename + LLM analysis done, analysis cached       | `ingestion.merge_graph_and_retitle` (inline); `ingestion.auto_generate_flashcards` (bg, flag); `plans.mark_stale_on_new_content` (inline)                                            |
| `QuizAttempted`          | Quiz scored + attempt persisted                   | `study.update_mastery`, `study.update_profile_from_quiz`, `study.record_activity`, `study.detect_weak_topics` (gated); `plans.mark_progress_from_quiz`; `generation.record_activity` |
| `FlashcardsReviewed`     | Single-deck review persisted                      | `study.update_mastery`, `study.update_profile_from_cards`, `study.record_activity`                                                                                                   |
| `StudySessionReviewed`   | Composed session results persisted                | `study.update_mastery`, `study.update_profile_from_cards`, `study.record_activity`; `plans.mark_progress_from_session`                                                               |
| `GenerationCompleted`    | A ContentItem was generated (user-requested only) | `generation.record_activity`; `plans.mark_progress_from_generation`                                                                                                                  |
| `ActivitiesLogged`       | Frontend telemetry batch flushed                  | `activity.log_activities` (ledger); `activity.distill_engagement`                                                                                                                    |
| `StudyPlanStaleDetected` | A module's plan no longer matches reality         | `plans.regenerate_stale_plan` (bg, throttled)                                                                                                                                        |

### Event-Trigger Map

```
USER ACTION                  EVENT PUBLISHED            REACTIONS (handlers)
────────────────────────────────────────────────────────────────────────────

Upload text/PDF/office  →   DocumentIngested       →   ingest_document (bg):
(file converted to PDF                               office→PDF already done
 via LibreOffice at upload;                          at upload; extract_text
 text extracted sync)                                → auto-rename (own commit)
                                                     → LLM analysis → cache
                                                     → publishes DocumentAnalyzed

Upload audio            →   DocumentIngested       →   ingest_document (bg):
                                                     Qwen3-ASR transcription
                                                     (chunk if >25MB)
                                                     → rename → analysis → …
                                                     → DocumentAnalyzed

Document analyzed       →   DocumentAnalyzed       →   merge concept graph +
                                                     auto-title lectures (inline)
                                                     auto-flashcards (bg,
                                                       flag-gated, origin="auto")
                                                     mark module plan stale

Click "Generate"        →   (SSE stream runs the      analyze → retrieve →
                            pipeline) then             plan → generate →
                            GenerationCompleted       validate → finalize
                                                   +   session action; plan
                                                       generate_* items done

Submit quiz answers     →   QuizAttempted          →   mastery + FSRS + latency
                                                     (per question, always-on)
                                                     learner_profile (+ pacing)
                                                     session action
                                                     weak_topics + counter
                                                       (gated: score < 0.7)
                                                     plan: take_quiz done,
                                                       plan → stale

Review flashcards /     →   FlashcardsReviewed /   →   mastery + FSRS + latency
composed session            StudySessionReviewed       profile (known-ratio)
                                                     session action
                                                     plan: review items done
                                                       (≥50% concept coverage)

Open home screen        →   (no event — read path)     GET /api/recommend →
                                                     build_context → decide
                                                     impression logged (with
                                                     bandit feature vector)

Click / dismiss rec     →   (no event — logged as      reward computed on
                            RecommendationEvent)       interaction event

Frontend telemetry      →   ActivitiesLogged       →   user_activities ledger
(batched flush)                                      (5,000-row cap)
                                                   +   distill into engagement /
                                                       study_patterns keys
```

---

## 5. Concept Mastery & FSRS

`concept_mastery` is the **convergence point** of the entire system — written by
quiz, flashcard, and concept-graph flows; read by generation, recommendations,
study plans, and the concepts API.

### Data Structure

```python
concept_mastery = {
    "Calvin cycle": {
        "correct": 3,
        "wrong": 2,
        "seen": 5,
        "mastery_pct": 0.6,           # correct/seen ratio
        "latency": {
            "avg_secs": 34.0,         # rolling answer latency
            "samples": 5,             # slow-and-wrong is a stronger
        },                            # weakness signal than either alone
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

Runs inside the `study.update_mastery` handler for every answered question or
reviewed card (all three study events feed the same handler):

```
QuizAttempted / FlashcardsReviewed / StudySessionReviewed
  │
  ▼
events/handlers/study.update_mastery
  │  calls: update_concept_mastery(session, concept, correct, latency_secs)
  │
  ▼
memory.update_concept_mastery()
  │
  ├── 1. Increment tally: seen++, correct++ or wrong++
  ├── 2. Recompute: mastery_pct = correct / seen
  ├── 3. Update rolling latency: {avg_secs, samples}
  ├── 4. Infer FSRS rating: correct → Good(3), wrong → Again(1)
  ├── 5. Load existing fsrs state from concept entry
  ├── 6. Call fsrs_scheduler.schedule_review(existing_fsrs, rating)
  │        │
  │        ▼
  │    fsrs library: Scheduler.review_card(card, rating)
  │        → updates stability, difficulty, due date
  │        → returns new state dict
  │        │
  │        ▼
  │    New FSRS state: {stability, difficulty, due, last_review, state, step}
  │
  ├── 7. Write updated concept entry back to concept_mastery
  │
  ▼
OUTPUT: concept's mastery_pct and latency reflect the latest answer.
        concept's FSRS due date is now scheduled at the optimal interval.
        Next /api/recommend, /api/concepts, or planner call sees the new state.
```

### FSRS Rating Mapping

| User Action                   | FSRS Rating | Effect on scheduling                          |
| ----------------------------- | ----------- | --------------------------------------------- |
| "I know this" / quiz correct  | Good (3)    | Stability grows → next review further out     |
| "Still learning" / quiz wrong | Again (1)   | Stability drops → review again soon (minutes) |

### Retrievability R(t)

`fsrs_scheduler.retrievability(state, now)` delegates to the FSRS library's
native forgetting curve — the power law
**R(t) = (1 + FACTOR·elapsed/stability)^DECAY** — an estimate of recall
probability right now. It is the continuous urgency signal used to:

- rank due concepts in the session composer (§6, blended with per-concept
  difficulty into a failure-risk score — most at risk first)
- rank the due-review deck the recommender surfaces (§7)
- surface "average recall" on the concepts dashboard
- ground the planner's weakest-concepts packet (§8)

> **Calibration (measured, see §13):** on real forgetting-curve traces
> (Duolingo HLR) this calibrates at Brier ≈ 0.08 / log-loss ≈ 0.31 — usable
> as a probability. The eval suite gates the wrapper against the library
> curve and absolute calibration bars, so the old hand-rolled exp(−t/S)
> approximation (Brier 0.3–0.6) cannot silently return.

---

## 6. Study Session Composer

`POST /api/study-session` composes the optimal mixed session instantly from
pre-generated decks — **no LLM call at session time**.

```
COMPOSE (scope: global | document | module)
  │
  ├── 1. Load all flashcard decks in scope; index concept → cards
  │
  ├── 2. Classify concepts against concept_mastery:
  │       seen=0 / no fsrs          → NEW
  │       fsrs due now              → REVIEW (with retrievability R)
  │       not due                   → skip (review later at optimal time)
  │
  ├── 3. Rank review concepts by R ascending — most-forgotten first
  │
  ├── 4. Mix ratio from last-5 quiz accuracy (desirable difficulty):
  │       accuracy < 0.70  → 80% review / 20% new   (struggling → reinforce)
  │       accuracy > 0.85  → 40% review / 60% new   (mastering → push forward)
  │       otherwise        → 60% review / 40% new   (the 70-85% target zone)
  │       (shortage fallback shifts slots to whichever pool has cards)
  │
  └── 5. Emit cards tagged source="review"|"new" + mix + rationale

SUBMIT → POST /api/study-session/{id}/review → publishes StudySessionReviewed
```

Cards carry 2–3 phrasing **variants per concept** (generated at generation
time) so the same concept can be re-tested from different angles, defeating
pattern-matching (recognition) in favor of genuine recall.

---

## 7. Concept Knowledge Graph

### What it is

When a document is ingested (text extracted or audio transcribed), the
ingestion chain extracts not just concepts but their **relationships** —
prerequisites, related concepts, and part-of links. These merge into
`concept_mastery` as additive fields (`prerequisites`, `related`, `documents`,
`modules`).

### Build Flow (event-driven)

```
DOCUMENT UPLOADED
  │
  ▼
bus.publish(DocumentIngested)  →  ingest_document (background)
  │
  ├── Text/PDF/office? ── text extracted at upload; office formats
  │                       converted to PDF via LibreOffice headless first
  │
  ├── Audio? ──────────── Qwen3-ASR transcription → writes doc.text
  │
  ├── auto-rename ─────── heuristic gate (hex hashes, IMG_1234, timestamps)
  │                       + one small LLM call → clean title (own commit,
  │                       runs BEFORE analysis so a flaky analysis can't
  │                       block it)
  │
  ├── LLM analysis ────── tools.analyze_document(doc.text)
  │                       → concepts[], concept_relationships[]
  │                         (prerequisite, related, part_of edges)
  │
  ├── cache analysis in doc memory (key="analysis")  [committed]
  │
  ▼
bus.publish(DocumentAnalyzed)
  │
  ├── merge_graph_and_retitle (inline):
  │     merge_concept_graph(session, doc_id, analysis)
  │       For each concept:
  │         → Ensure concept_mastery entry exists
  │         → Add doc_id to entry.documents[]
  │         → Add module title to entry.modules[]
  │       For each relationship:
  │         → prerequisite → add to entry.prerequisites[]
  │         → related/part_of → add to entry.related[]
  │     + auto-title generic lectures from the analysis topic
  │
  └── auto_generate_flashcards (background, auto_generate_flashcards flag):
        full agent pipeline, deck tagged origin="auto", deduped per doc
```

### Ambient Intelligence Pattern

This is the Claude Code / Hermes pattern: the agent works in the background,
accumulating knowledge while the user moves on. The user uploads, walks away,
and returns to find the concept graph already built — 18 concepts and 23
prerequisite relationships merged silently in ~25 seconds. The analysis is
cached, so the first "Generate" click is instant.

The agent uses this structural knowledge during generation: if "Calvin cycle"
has a prerequisite on "Carbon fixation" and the learner's mastery of Carbon
fixation is low, the planner tests the foundation first. (Traversal is
currently one-directional — a known limitation.)

---

## 8. Study Plans

Per-module, exam-paced study plans: a commitment about the _future_ that
adapts as the semester accretes. One `StudyPlan` row per module (unique
`module_id`), versioned in place.

### Generation (deterministic grounding → one LLM call → validation)

```
POST /api/modules/{id}/plan  (or background regeneration)
  │
  ▼
planner.generate_study_plan(module_id)
  │
  ├── 1. GROUNDING (deterministic, everything the prompt may know):
  │       module documents (topic, has_quiz, has_deck, read state)
  │       module concepts — mastery entries whose documents[] intersect the
  │         module's doc ids (id-based: the title-based modules[] field
  │         would orphan on rename)
  │       FSRS summary (due now, per-concept retrievability, weakest 10
  │         with prerequisite chains)
  │       weak topics, learner profile (incl. flashcard_known_ratio),
  │       learner insights summary, engagement (unread docs)
  │       days to exam + horizon (≤14 days cap, default 7)
  │
  ├── 2. ONE LLM CALL (temperature 0.2) → day-bucketed items of six
  │       constrained types: review_concepts · take_quiz · generate_quiz ·
  │       review_deck · generate_flashcards · read_document
  │       Prompt rules: only reference input documents/concepts; sequence
  │       along prerequisite chains; interleave review with new material;
  │       ≤45 min/day; rationales must cite their evidence.
  │
  ├── 3. VALIDATION (no LLM):
  │       type whitelist; title→id resolution — items referencing
  │       documents that don't resolve are silently dropped (hallucinations
  │       never reach the UI); day_offset clamped to horizon; estimate
  │       clamped 5–90 min; empty plan gets a fallback review item.
  │
  └── 4. PROGRESS CARRY-OVER: done items from the previous version are
          matched by (type, document, concepts) and preserved —
          regenerating mid-semester never loses completed work.
```

Each item: `{id, type, title, rationale, day_offset, estimate_mins, status,
done_at, done_reason, done_kind, target: {document_id, concepts}}`.

### Adaptation (on the bus)

```
DocumentAnalyzed (module doc)  ─┐
QuizAttempted (module doc)      ─┼─→ append stale_reasons → publish
                                │   StudyPlanStaleDetected
                                ▼
              regenerate_stale_plan (background)
                └── regenerates ONLY if plan.stale_reasons non-empty
                    AND ≥24h since last regeneration (per-module cooldown —
                    an agent that replans on every uploaded slide is as
                    useless as one that never replans)
```

Read-time staleness is computed on `GET /api/modules/{id}/plan`: if the exam
is ≤7 days away and the plan is ≥2 days old, it's flagged stale.

### Progress auto-detection

The plan notices when it's followed — no separate reporting:

| Trigger                                 | Marks done                                                              |
| --------------------------------------- | ----------------------------------------------------------------------- |
| `QuizAttempted` on a module doc         | matching `take_quiz` / `review_deck` items (`auto · Quiz taken — 6/10`) |
| `GenerationCompleted` (quiz/flashcards) | matching `generate_quiz` / `generate_flashcards` items                  |
| `StudySessionReviewed`                  | any `review_concepts` item whose concepts are ≥50% covered              |
| Manual checkbox                         | any item (`PATCH /api/plans/{plan_id}/items/{item_id}`)                 |

Today's / overdue pending items also surface on the home card via the
`plan_today` recommendation strategy (§11) — one voice, not two agents
disagreeing on the front door.

---

## 9. Behavior Tracking & Reflection

### Actions as events (frontend)

A `track()` helper (`frontend/src/api/track.ts`) buffers in-app interactions —
document opens, dwell, quiz pacing, abandonment, concept curiosity — and
flushes them in batches: `fetch` keepalive normally, `navigator.sendBeacon` on
tab close (posts `text/plain`, skipping the CORS preflight — the endpoint
parses the raw body itself). `POST /api/activity` returns 202 and publishes one
`ActivitiesLogged` event per flush (~15 dot-namespaced action types, from
`document.opened` to `recording.discarded`).

### Distillation (deterministic) + ledger

Two independent handlers per flush:

- **`log_activities`** — appends to the `user_activities` ledger table,
  pruned to the newest 5,000 rows.
- **`distill_engagement`** — folds the batch into two memory keys:
  `engagement` (per-doc views, dwell, last-viewed) and `study_patterns`
  (hour-of-day histogram → best study hour, avg quiz duration,
  completed/abandoned sessions).

Per-question latency rides the same study events as correctness and settles
into the same mastery entries as the FSRS state (rolling `{avg_secs, samples}`
per concept). The generation prompts render it directly:
`Calvin cycle: 3/5 correct [weak] · slow recall (~34s avg)`.

### Reflection (one LLM call, grounded)

`reflection.reflect_on_learner` reads the deterministic signals — profile,
activity-type counts, hour histogram, dwell, weakest/strongest/slowest
concepts, weak topics — and writes the `learner_insights` memory key
(`{summary, traits, habits}`). Prompt rules: base every claim on the provided
data; never invent; if a signal is empty, say nothing about it; be specific
and behavioral; keep it kind (it's shown to the student).

**Cooldown:** runs only when ≥25 new ledger rows accumulated AND ≥1h since the
last run — unless forced via `POST /api/memory/reflect`.

### The mirror

The sidebar profile card opens into **"How the agent sees you"**
(`UnderstandingModal`): the reflection's summary and trait chips, study
patterns, slow-recall concepts, and a refresh button. It renders the same
memory keys the generation prompts consume — what the student reads is a
strict subset of what the agent uses.

---

## 10. Proactive Agent

A background asyncio loop from `main.py` lifespan, every 30 minutes
(configurable), only spawned when `proactive_enabled=true` (checked each tick,
so the flag toggles without restart). Each tick runs three jobs:

```
proactive_loop:
  while True:
    sleep(proactive_interval_seconds)  # 30 min default
    if not proactive_enabled: continue
    │
    ├── 1. run_proactive_review()
    │     ├── get_review_candidates(session, cooldown_hours=24)
    │     │     For each analyzed document:
    │     │       weak = weak_topics ∩ doc_concepts
    │     │       due  = FSRS-due concepts ∩ doc_concepts
    │     │       Skip if a proactive deck exists within cooldown (24h)
    │     └── for each candidate:
    │           run_generation(task_type="flashcards",
    │             instructions="Review deck targeting: <concepts>")
    │           → full agent pipeline runs
    │           → tag ContentItem: origin="proactive"
    │
    ├── 2. reflect_on_learner(session)   (cooldown-gated, §9)
    │
    └── 3. run_bandit_update()           (§11 telemetry → ML loop)
```

### Config

| Setting                      | Default         | Effect                                                                 |
| ---------------------------- | --------------- | ---------------------------------------------------------------------- |
| `proactive_enabled`          | `false`         | Master switch for the loop                                             |
| `proactive_interval_seconds` | `1800` (30 min) | How often the loop runs                                                |
| `proactive_score_threshold`  | `0.7`           | Quiz score below this = "struggled" → weak_topics                      |
| `proactive_cooldown_hours`   | `24`            | Don't regenerate a deck for the same doc within this window            |
| `auto_generate_flashcards`   | `false`         | Auto-generate a deck after each document is analyzed (`origin="auto"`) |
| `auto_rename_files`          | `true`          | Rename machine-generated filenames after analysis                      |

---

## 11. Recommendation Engine

A plugin-based strategy engine that recommends what to study next. Each
strategy self-describes its priority against the `UserContext`; the engine
iterates and scores. **Deterministic by design** — the panel polls every 15s,
so no LLM in the hot path; scoring consumes measurements, never the LLM's
prose.

### Decision Flow

```
GET /api/recommend
  │
  ▼
build_context(session) → composes UserContext from all signals:
  │  ├── due_concepts (FSRS-due) + due_cards
  │  ├── concept_mastery + weak_topics + slow_concepts (latency)
  │  ├── learner_profile (level, difficulty, formats, goal, known-ratio)
  │  ├── documents + content coverage + analyses
  │  ├── proactive_decks
  │  ├── behavioral: engagement, study_patterns, insights summary (unscored)
  │  ├── computed: neglected_docs (analyzed, never opened), peak_hour
  │  ├── plan_today (today's/overdue pending plan items; ≤2/module, ≤3 total)
  │  └── exam: days_to_exam + doc_exam_days (per-document urgency)
  │
  ▼
engine.decide(ctx):
  │
  ├── 1. EVALUATE all strategies (12 registered):
  │     strategy.evaluate(ctx) → score 0.0-1.0 or None
  │
  ├── 2. FATIGUE PENALTY on practice (if session active):
  │     fresh (<20m): 0 · focused (20-50m): −0.05 · fatigued (50m+): −0.20
  │
  ├── 3. PEAK-HOUR BOOST (+0.05 on practice): near the learner's
  │     habitual study hour (the fatigue penalty's optimistic mirror)
  │
  ├── 4. EXAM URGENCY RAMP (up to +0.10): practice on exam-module
  │     documents inside a 14-day window — full boost on exam day,
  │     fading linearly to zero at the horizon
  │
  ├── 5. DISMISSAL PENALTY: dismissed strategy this session → score × 0.1
  │
  ├── 6. EPSILON-GREEDY EXPLORATION (10% chance): boost a random
  │     non-top result by +0.5 (fresh telemetry for the bandit)
  │
  ├── 7. SORT by score descending
  │
  └── 8. PRIMARY = top result
        ALTERNATIVES = top of each complementary category (≤2)
        + context summary + impression_id
```

### Strategy Scoring Table

| Strategy          | Base      | Condition                                                              | Boosts / notes                                                                           |
| ----------------- | --------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Onboarding        | 1.0       | No documents                                                           | Not dismissible                                                                          |
| DueReviewReady    | 0.95      | Due concepts + existing cards                                          | Soft override; rationale annotates slow-recall due concepts                              |
| PlanToday         | 0.92      | Today's / overdue plan items                                           | Top authority under FSRS-due reviews; carries the plan's title, rationale, time estimate |
| DueReviewGenerate | 0.85      | Due concepts, no cards                                                 | —                                                                                        |
| ProactiveDeck     | 0.80      | Unseen proactive deck                                                  | Ranked by overlap with slow/weak concepts                                                |
| QuizGap           | 0.60      | Notes exist, no quiz                                                   | Neglected (never-opened) docs jump the queue                                             |
| WeakSpot          | 0.55–0.75 | ≥2 slow-recall concepts (or 1 + weak-topic corroboration), nothing due | Latency-grounded targeted quiz; defers to due reviews                                    |
| StartNotes        | 0.55      | Doc with no content                                                    | Neglected docs jump the queue                                                            |
| Revisit           | 0.40      | Previously-read doc untouched ≥5 days                                  | "You haven't revisited this in N days"                                                   |
| Quiz              | 0.30      | Documents exist                                                        | +0.40 after flashcards (chaining), +0.10 momentum, −0.05 format tilt                     |
| Flashcard         | 0.25      | Documents exist                                                        | +min(due_count/20, 0.30) FSRS urgency, +0.10 format tilt                                 |
| Fallback          | 0.10      | Always (documents exist)                                               | —                                                                                        |

**Format tilt** (grounded in `study_patterns`): quizzes averaging >2 min or
more abandoned than completed sessions shift practice toward flashcards.

### Telemetry → ML Loop

```
IMPRESSION                      INTERACTION                    BANDIT UPDATE
(GET /api/recommend)            (user clicks/dismisses)         (proactive loop)
                                                                │
  → log RecommendationEvent       → POST /api/recommend/feedback → load clicked events
    (event_type="impression")       → calculate_reward()          with rewards
    + store the bandit FEATURE      → log RecommendationEvent   → ridge regression
      VECTOR at impression time       (event_type=action)         per strategy (6 features:
    [one per shown rec]                                            bias, fsrs_urgency, fatigue,
                                                                   chaining, exam_urgency,
REWARD VALUES:                                                    mastery_gap)
  completed = +1.0                                              → update A, b, W
  clicked   = +0.4                                              → clamp [-1.0, 2.0]
  dismissed = -0.5                                              → write bandit_weights
  abandoned = -0.1
```

> **Honest gap:** the bandit now genuinely runs and fits weights from real
> feature vectors stored at impression time (the context that produced a
> recommendation can't be reconstructed later — half its features are session
> state). `decide()` does **not** yet read the weights when scoring; the fit
> results are logged but unused. Wiring them in is the next closure.

---

## 12. Learner Profile

Automatically inferred from behavior — no forms, no onboarding. Four dimensions:

| Dimension                | Values                               | How Inferred                                                                                  |
| ------------------------ | ------------------------------------ | --------------------------------------------------------------------------------------------- |
| **learner_level**        | beginner / intermediate / advanced   | Difficulty-weighted avg quiz score (last 10). Acing hard docs → advanced.                     |
| **preferred_difficulty** | easy / medium / hard                 | Drifts from level: last 5 scores >80% → bump up, <50% → ease off                              |
| **preferred_formats**    | quiz_length, card_style, notes_depth | From hint input (persisted): "10 questions" → quiz_length=10, "concise" → notes_depth=concise |
| **study_goal**           | exam_prep / casual / skill_building  | Cadence: 3+ quizzes within 6h → exam_prep. Hint keywords.                                     |

All inference is **deterministic** (no LLM) — pure math, thresholds, and regex.
Updated on every quiz submit, flashcard review, and generate hint.

The `stats` block carries computed measurements: `score_history` (rendered as
a first-half-vs-second-half trend, silent when flat — a fabricated trend is
worse than none), `avg_score`, `total_quizzes`, and `flashcard_known_ratio`
(rolling average of known-cards on review batches).

### How the Profile Flows Into Generation

```
retrieve_memory node
  │
  ├── reads learner_profile + learner_insights from agent_memory
  │
  ▼
_memory_hint (tools.py)
  │
  ├── renders: "Intermediate learner, avg 72%, scores trending up (35%→53%),
  │            knows ~80% of reviewed flashcards — favor application-style
  │            over definition cards, prefers medium difficulty."
  │            + per-concept mastery labels (VERY WEAK / weak / strong),
  │              ⚡ DUE markers, slow-recall latency, prerequisite chains
  ▼
plan_task system prompt
  │
  ├── "Use the learner_profile to calibrate: match preferred_difficulty
  │   for the difficulty mix. Use preferred_formats as defaults."
  │
  ▼
Generated content is personalized (difficulty, format, depth, style)
```

The same profile (plus the known-ratio) grounds the study planner (§8).

---

## 13. Evals

An LLM eval harness at `backend/evals/` — pytest suites (`@pytest.mark.evals`)
that call the **real production code** with **real LLM calls** against public
datasets, so improvements and regressions in the agent are measured, not felt.

### Design rules

- **Suites run production chains, not mocks** — e.g. the quiz suite executes
  the same `analyze → plan → generate` LLM calls the LangGraph pipeline makes.
- **The judge is a stronger model than the generator** (`evals_judge_model`,
  default `deepseek/deepseek-v4-flash-0731`, temperature 0, routed through the app's
  OpenRouter client via a DeepEval adapter) — a model never grades its own
  failure modes.
- **Deterministic metrics where possible**: fuzzy concept F1 (rapidfuzz
  token-set ≥85), ROUGE-1, AUC / Brier / log-loss / majority-accuracy. The
  LLM judge (DeepEval GEval rubrics) only scores what determinism can't
  (groundedness, distractor plausibility, atomicity, rationale evidence).
- **Comparable runs**: fixed sample seed (42), `EVALS_N` caps cases per suite
  (default 10).

### Suites

| Suite        | Measures                                                                                                                    | Dataset                              |
| ------------ | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| `analysis`   | Concept extraction vs gold lists + prerequisite edges; difficulty calibration                                               | AL-CPL (+ Wikipedia summaries), RACE |
| `quiz`       | Structure, groundedness, distractor plausibility, concept tags, novice-vs-advanced personalization                          | SciQ                                 |
| `flashcards` | Matuschak-style rubric (atomicity, grounding); application-style tilt when known-ratio is high                              | SciQ                                 |
| `notes`      | Markdown structure, ROUGE-1 vs gold abstracts, judge faithfulness, key-point coverage                                       | PubMed summarization                 |
| `planner`    | Plan invariants (type whitelist, horizon, daily load, weak-engaged-early) + judge: rationale cites evidence (≥0.60)         | synthetic modules seeded from SciQ   |
| `reflection` | No-fabricated-numbers grounding + judge faithfulness                                                                        | synthetic behavior archetypes        |
| `rename`     | Heuristic gate recall, rule compliance, judge descriptiveness                                                               | synthetic filenames                  |
| `session`    | Composer property tests: mix ratios, ordering, fallbacks, scopes (no LLM)                                                   | —                                    |
| `fsrs`       | Replays real forgetting traces through the production scheduler; AUC/Brier vs power-law, streak, and correct-rate baselines | Duolingo HLR (13M traces)            |
| `recommend`  | Replays real learner logs through the real `engine.decide()`; invariants + weakness precision vs random                     | EdNet-KT1                            |

Notable recorded findings (report-only metrics, kept visible on purpose):

- The app's original exp(-t/S) retrievability was **miscalibrated as a
  probability** (Brier 0.3–0.6 vs the power-law's ~0.08) — fixed 2026-08:
  the wrapper now delegates to the library's power-law curve, gated against
  drift (§5's caveat is retired).
- FSRS with default parameters ranks within sampling noise of last-outcome
  streaks, and does **not** beat the running correct-rate baseline on
  Duolingo material (~93% base success rate; the defaults were fit on Anki
  data). Due-review decks and sessions therefore rank concepts by a
  failure-risk blend (correct-rate + forgetting curve).
- On EdNet replay, the engine's weakness-precision lift over random
  targeting was negative — a diagnostic showed 82% of risk-ranked due
  concepts were never attempted again (counting as non-failures) while
  the conditional failure prediction already beat random. Fixed 2026-08:
  due decks (recommender + session composer) tier recently-active
  concepts (attempted ≤7 days ago, tracked per concept) ahead of idle
  ones, ranked by failure risk within tiers. Lift now positive on train,
  val, and held-out test (+0.04 to +0.08).
- The reflection narrative **contradicted its grounding packet** on a
  synthetic archetype (claimed "has not reviewed any flashcards" over eight
  flashcard activities) — fixed 2026-08 with a labeled packet rendering,
  hardened prompt rules, and a self-verify pass; the faithfulness gate rose
  from a 0.45 floor to 0.60.

### Running

```
task study-app:evals-prepare   # download + prepare datasets → evals/data/ (gitignored)
task study-app:evals           # pytest evals/ -m evals (EVALS_N caps cases/suite)
```

Reports land in `evals/reports/<run>/` + `reports/latest/`; `EVALS.md` is
regenerated with deltas vs committed baselines (`reports/baselines/`, promoted
via `python -m evals.report --promote`). See `evals/README.md` for details.

---

## 14. Complete Flow Diagrams

### Quiz Submission — Full Chain

```
USER SUBMITS QUIZ ANSWERS
  │
  ▼
POST /api/quiz/{content_id}/attempt
  │
  ├── 1. SCORE: for each question, is_correct = answers[q.id] == q.answer_idx
  ├── 2. PERSIST: QuizAttempt row (score, answers, per-question latency, timestamp)
  ├── 3. COMMIT — the attempt is durable before anything automatic runs
  │
  ▼
bus.publish(QuizAttempted(results=[QuestionOutcome(latency_secs=…)]))
  │
  ├── 4. MASTERY (always-on, per question):
  │      study.update_mastery → update_concept_mastery
  │      → tally + FSRS scheduling + rolling latency {avg_secs, samples}
  │
  ├── 5. PROFILE (always-on):
  │      study.update_profile_from_quiz → recompute level, drift difficulty,
  │      update stats; quiz duration → study_patterns pacing history
  │
  ├── 6. SESSION: study.record_action("quiz", doc_id)
  │      → action chaining + fatigue for the recommender
  │
  ├── 7. WEAK TOPICS (gated: proactive_enabled AND score < 0.7):
  │      study.detect_weak_topics → match missed prompts against doc
  │      concepts → add_weak_topics(); increment doc quiz_attempts counter
  │
  ├── 8. PLAN: plans.mark_progress_from_quiz
  │      → matching take_quiz/review_deck items done (auto)
  │      → plan marked stale (mastery shifted) → StudyPlanStaleDetected
  │
  └── each handler: own transaction, ok/failed row in agent_events
```

### Document Upload — Text/PDF/Office vs Audio

```
UPLOAD REQUEST (POST /api/documents, optional lesson_id/module_id — the
frontend prompts to file unfiled uploads at the moment of creation)
  │
  ├── Is it audio? (.webm/.mp3/.m4a/.wav/.ogg)
  │   ├── Save file to storage/
  │   ├── Create Document(kind="audio", text="", transcription_status="pending")
  │   ├── Commit + return 201 immediately
  │   └── publish DocumentIngested(source="audio")
  │       → ingest_document (bg): Qwen3-ASR transcription (chunk if >25MB)
  │         → rename → analysis → DocumentAnalyzed → … (same chain below)
  │
  └── Text/PDF/MD — or office (.pptx/.docx/.xlsx/.doc/.ppt)?
      ├── Office? → convert to PDF via LibreOffice headless (subprocess);
      │   the converted PDF is the stored artifact (react-pdf viewer)
      ├── Save file to storage/
      ├── extract_text (PyMuPDF for PDF, read_text for txt/md)
      ├── Create Document(kind="text", text=extracted_text)
      ├── Commit + return 201
      └── publish DocumentIngested(source="upload")
          → ingest_document (bg): auto-rename (own commit) → LLM analysis
            → cache (commit) → publish DocumentAnalyzed
               ├── merge concept graph + auto-title lectures (inline)
               ├── auto-generate flashcards (bg, flag-gated, origin="auto")
               └── mark module study plan stale
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
  ├── event: status  "Recalling what you know…"  ← retrieve_memory node
  ├── event: status  "Planning what to create…"  ← plan node
  ├── event: status  "Creating your quiz…"       ← generate node
  ├── event: status  "Checking the quality…"     ← validate node
  ├── event: status  "Saving the results…"       ← finalize node
  │
  └── event: done    {item: ContentItem}
      → publish GenerationCompleted
         ├── session action recorded (chaining/fatigue)
         └── matching generate_* plan items marked done
      → Frontend invalidates ["recommend"] → re-fetches recommendations
```

---

## 15. Background Tasks

Background work takes two shapes: the long-running proactive loop, and
tracked background handlers spawned by the event bus (each visible as a row in
`agent_events` while in flight).

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. PROACTIVE LOOP (long-running, app lifetime)                          │
│    Spawn: main.py lifespan → asyncio.create_task(proactive_loop())      │
│    Guard: only spawned if proactive_enabled=true; re-checked per tick   │
│    Schedule: every proactive_interval_seconds (30 min default)          │
│    Does: run_proactive_review() + reflect_on_learner() +                │
│          run_bandit_update()                                            │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 2. INGESTION CHAIN (per upload, background handler on DocumentIngested) │
│    audio → Qwen3-ASR transcription → auto-rename → LLM analysis →       │
│    cache → DocumentAnalyzed → graph merge (inline) +                    │
│    auto-flashcards (bg, flag) + plan staleness (inline)                 │
│    Lifetime: one chain per document, ~20-30s for analysis               │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ 3. PLAN REGENERATION (background handler on StudyPlanStaleDetected)     │
│    Regenerates a stale module plan — at most once per module per 24h    │
│    (LLM cost control). Progress carries over across versions.           │
└─────────────────────────────────────────────────────────────────────────┘

All background handlers are tracked asyncio tasks (exceptions logged, never
swallowed) and audited in agent_events — GET /api/events shows dispatch,
ok, and failed rows for each.
```

---

## 16. Frontend Architecture

### Route Structure (TanStack Router, code-based)

```
/                                    → RecommendationPanel (home)
  │                                     └── polls /api/recommend every 15s
  │
  ├── /documents/$docId               → redirect to /documents/$docId/document
  │
  ├── /documents/$docId/$tab          → DocTabView
  │       ├── tab=document            → DocumentView (react-pdf viewer or
  │       │                              audio player; source/text toggle)
  │       ├── tab=notes               → NotesView (markdown render) + Generate bar
  │       ├── tab=quiz                → QuizView (interactive MCQ, timed) + Generate bar
  │       └── tab=flashcards          → FlashcardView (3D flip, version picker)
  │                                      + Generate bar
  ├── /record                         → RecordPage (focused recording: slides,
  │                                      notes, timestamp stamping — no sidebar)
  ├── /lecture/$lectureId             → LectureView (immersive playback,
  │                                      auto-advancing slides — no sidebar)
  ├── /study                          → StudySessionView (composed session,
  │                                      no sidebar)
  ├── /concepts                       → ConceptsPage (dashboard: summary line,
  │                                      filters, reference modal)
  ├── /modules                        → ModulesPage (semester-organized browser:
  │                                      Autumn/Spring/Summer × academic year,
  │                                      "This semester" computed + expanded,
  │                                      drag-and-drop filing, module plans)
  ├── /quizzes                        → QuizzesPage (all quizzes)
  └── /flashcards                     → FlashcardsPage (all decks)
```

### Layout

```
┌──────────────┬──────────────────────────────────────────────┐
│              │                                              │
│  SIDEBAR     │              MAIN CONTENT                     │
│              │                                              │
│  ┌────────┐  │  ┌──────────────────────────────────────┐   │
│  │ 📚 Logo│  │  │                                      │   │
│  └────────┘  │  │  Route content (see tree above)     │   │
│              │  │                                      │   │
│  Nav buttons │  └──────────────────────────────────────┘   │
│  Home        │                                              │
│  Record      │                                              │
│  Concepts    │                                              │
│  Modules     │                                              │
│  Quizzes     │                                              │
│  Flashcards  │                                              │
│              │                                              │
│  ProfileCard │                                              │
│  → "How the  │                                              │
│    agent     │                                              │
│    sees you" │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

The sidebar collapsed from a document tree + management UI to navigation
buttons + profile card when the Modules page took over organization: the
sidebar is for getting around; the main area is for working.

### Key Frontend Patterns

- **TanStack Query** for all server state — polls, invalidation, optimistic updates
- **XHR upload** with `onprogress` for large files
- **MediaRecorder API** for in-browser recording; slide clicks stamped onto the audio timeline
- **SSE via fetch + ReadableStream** for generation status streaming
- **react-pdf** for rendered originals; one-toggle switch to extracted text
- **Batched telemetry**: `track()` buffers actions, flushes via fetch keepalive / `navigator.sendBeacon`
- **localStorage** for tree expansion state + proactive deck dismissal
- **Module-level `pendingGenerate` flag** for cross-component generation trigger (recommendation panel → DocTabView)

---

## Appendix: File Map

```
study-app/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, lifespan, 14 routers
│   │   ├── config.py               # Settings (OpenRouter, ASR, evals, proactive, auto-*)
│   │   ├── db.py                   # SQLAlchemy async engine (WAL) + migrations
│   │   ├── models.py               # Module, Lesson, Document, ContentItem,
│   │   │                           #   QuizAttempt, AgentMemory, RecommendationEvent,
│   │   │                           #   AgentEvent, UserActivity, StudyPlan, LectureSession
│   │   ├── schemas.py              # Pydantic request/response models
│   │   ├── storage.py              # Filesystem layer for uploads (uuid names)
│   │   ├── parsers.py              # PyMuPDF extraction + LibreOffice → PDF
│   │   ├── transcription.py        # Qwen3-ASR via OpenRouter + chunking
│   │   ├── proactive.py            # Background loop: review decks + reflection + bandit
│   │   ├── llm.py                  # OpenRouter chat client (retry, JSON repair, model override)
│   │   ├── agent/
│   │   │   ├── graph.py            # LangGraph StateGraph + streaming
│   │   │   ├── state.py            # AgentState TypedDict
│   │   │   ├── nodes.py            # 6 pipeline nodes
│   │   │   ├── tools.py            # Generation tools, analysis, rename, _memory_hint
│   │   │   ├── memory.py           # All memory helpers (mastery, FSRS, profile)
│   │   │   ├── fsrs_scheduler.py   # FSRS wrapper + retrievability
│   │   │   ├── concept_graph.py    # Graph merge on analysis
│   │   │   ├── planner.py          # Per-module study plan generation
│   │   │   ├── behavior.py         # Deterministic behavior distillation
│   │   │   └── reflection.py       # Grounded LLM reflection → learner_insights
│   │   ├── events/
│   │   │   ├── bus.py              # In-process event bus + agent_events logging
│   │   │   ├── domain.py           # Domain event dataclasses
│   │   │   └── handlers/
│   │   │       ├── ingestion.py    # DocumentIngested/Analyzed chain
│   │   │       ├── study.py        # Mastery, profile, session, weak topics
│   │   │       ├── activity.py     # Behavior ledger + distillation
│   │   │       ├── plans.py        # Staleness, regeneration, progress
│   │   │       └── generation.py   # Generation as session activity
│   │   ├── recommend/
│   │   │   ├── context.py          # UserContext builder (+ exam urgency, plan_today)
│   │   │   ├── engine.py           # Strategy registry + decision loop
│   │   │   ├── session.py          # Session tracking (actions, fatigue)
│   │   │   ├── telemetry.py        # Impressions/interactions + feature vectors
│   │   │   ├── bandit.py           # LinUCB contextual bandit
│   │   │   └── strategies/         # 12 strategy implementations
│   │   └── routes/
│   │       ├── documents.py        # Upload, list, get, delete, file serve, slides
│   │       ├── generate.py         # Generate + SSE stream
│   │       ├── content.py          # Content CRUD
│   │       ├── quiz.py             # Quiz submit (score + publish)
│   │       ├── flashcards.py       # Flashcard review (publish)
│   │       ├── study_session.py    # Compose session + submit review
│   │       ├── modules.py          # Module/Lesson CRUD + semester tree
│   │       ├── plans.py            # Module plans: get/generate/check-off
│   │       ├── lectures.py         # Lecture sessions (record → playback)
│   │       ├── concepts.py         # Concept dashboard + references
│   │       ├── activity.py         # Batched telemetry ingest (202)
│   │       ├── events.py           # GET /api/events (audit ledger)
│   │       ├── memory.py           # Memory/profile debug + POST reflect
│   │       └── recommend.py        # Recommendation + feedback
│   ├── evals/                      # LLM eval harness (see §13 + evals/README.md)
│   │   ├── data.py                 # Dataset preparation (SciQ, RACE, AL-CPL, …)
│   │   ├── judge.py                # DeepEval GEval on the stronger judge model
│   │   ├── metrics.py              # concept F1, ROUGE-1, AUC/Brier/log-loss
│   │   ├── report.py               # reports/<run>/ + EVALS.md + baselines
│   │   ├── conftest.py             # Throwaway DB, flags off, real LLM calls
│   │   ├── EVALS.md                # Generated results report
│   │   └── suites/                 # 10 suites (analysis, quiz, flashcards,
│   │                               #   notes, planner, reflection, rename,
│   │                               #   session, fsrs, recommend)
│   └── tests/                      # Unit/integration (recommend, plans, bus, …)
│
├── frontend/
│   └── src/
│       ├── main.tsx                # App mount (RouterProvider)
│       ├── router.tsx              # TanStack Router route tree
│       ├── types.ts                # Shared TS types
│       ├── lib/semesters.ts        # Term ordering / current-semester logic
│       ├── api/
│       │   ├── client.ts           # Typed fetch wrapper + all API functions
│       │   └── track.ts            # Batched activity telemetry (sendBeacon)
│       ├── styles/global.css       # Full stylesheet
│       └── components/
│           ├── Sidebar.tsx         # Nav buttons + ProfileCard
│           ├── DocTabView.tsx      # Document view with tabs
│           ├── DocumentView.tsx    # react-pdf / audio player / transcript
│           ├── PdfViewer.tsx       # Scrollable PDF column (react-pdf)
│           ├── NotesView.tsx       # Markdown render
│           ├── QuizView.tsx        # Interactive MCQ with timing
│           ├── FlashcardView.tsx   # 3D flip cards + version picker
│           ├── ConceptsPage.tsx    # Concept dashboard (filters, summary)
│           ├── ConceptDetailModal.tsx # References behind one concept
│           ├── ModulesPage.tsx     # Semester-organized module browser
│           ├── ModulePlanPanel.tsx # Per-module study plan UI
│           ├── RecordPage.tsx      # Focused lecture recording
│           ├── LectureView.tsx     # Immersive lecture playback
│           ├── StudySessionView.tsx # Composed study session
│           ├── QuizzesPage.tsx     # All quizzes
│           ├── FlashcardsPage.tsx  # All decks
│           ├── RecommendationPanel.tsx # Home screen recommendations
│           ├── UnderstandingModal.tsx  # "How the agent sees you"
│           ├── FileToModuleModal.tsx   # Filing prompt at creation
│           └── ProfileCard.tsx     # Learner profile badge
│
└── README.md
```
