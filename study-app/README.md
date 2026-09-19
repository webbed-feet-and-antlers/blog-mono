# Study App — an agent-driven AI study tools POC

Upload a document (PDF/TXT/MD), then let an **AI agent** generate study notes,
a multiple-choice quiz, or a flashcard deck. The core experiment: the three
features are not three isolated LLM calls — they're tools wielded by one shared
agent. Because they share a backbone, improvements (memory, better planning,
feedback loops) lift all features at once.

This is a proof of concept for an **agent-driven product**: the user interacts
with product features, not a chatbot. The agent is the engine; the UI is the
interface.

## Architecture

```
┌─────────────────┐        ┌──────────────────────────────────────────┐
│  React frontend │  /api  │  FastAPI backend                         │
│  (Vite + TS)    │ ─────▶ │                                          │
│                 │        │  ┌────────────────────────────────────┐  │
│  • upload docs  │        │  │  LangGraph agent (shared backbone)  │  │
│  • notes view   │        │  │                                    │  │
│  • quiz play    │        │  │  analyze → retrieve_memory → plan   │  │
│  • card review  │        │  │      → generate → validate → finalize│ │
│                 │        │  │                                    │  │
│                 │        │  │  tools: notes / quiz / flashcards   │  │
│                 │        │  │  memory: per-doc + cross-doc        │  │
│                 │        │  └────────────────────────────────────┘  │
│                 │        │                                          │
│                 │        │  SQLite (default) or Postgres            │
│                 │        │  (DATABASE_URL: asyncpg, JSONB, RLS)     │
│                 │        │  + filesystem for uploads                │
└─────────────────┘        └──────────────────────────────────────────┘
                                      │
                                      ▼
                           OpenRouter (OpenAI-compatible)
                           default model: deepseek-v4-flash-0731
```

### The agent backbone (the point of the POC)

All three features run through one LangGraph `StateGraph`:

```
START → analyze_document → retrieve_memory → plan → generate → validate → finalize → END
```

| Node               | Job                                                                                                                                      |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `analyze_document` | Extract topic, concepts, structure, difficulty from the doc. Cached per-doc in memory so it only runs once.                              |
| `retrieve_memory`  | Pull prior learnings — quiz misses, style prefs, past generations — into the generation context. **This is the shared-backbone payoff.** |
| `plan`             | Given the requested feature + analysis + memory, _decide_ what to generate (e.g. "8 questions weighted to weak topics").                 |
| `generate`         | Dispatch to the feature tool (`generate_notes` / `generate_quiz` / `generate_flashcards`).                                               |
| `validate`         | Structural self-check (quiz has 4 options + valid answer, cards aren't empty, etc.). Bad output is rejected, not persisted.              |
| `finalize`         | Persist a `ContentItem` and write back what the agent learned to memory.                                                                 |

Agent memory is the moat: as you use the app, quiz performance feeds back into
better flashcards and notes. See `backend/app/agent/`.

### Why this matters

The conventional approach is `POST /quiz` → one LLM call → done. This POC tests
whether routing everything through one agent with shared memory produces a
better, more cohesive study experience — and whether that architecture is
worth the extra latency. The agent "knows" what you've studied and struggled
with, and every feature benefits.

## Tech stack

- **Backend:** Python 3.13, FastAPI, Pydantic, SQLAlchemy 2 (async — SQLite
  by default, Postgres via `DATABASE_URL`), Alembic migrations, LangGraph,
  OpenAI SDK (pointed at OpenRouter). Managed with `uv`.
- **Frontend:** React 19, TypeScript, Vite, TanStack Query, TanStack Router,
  react-markdown.
- **LLM:** OpenRouter (one key, all providers). Default model is
  `deepseek/deepseek-v4-flash-0731` — cheap and fast. Swap by changing one env var.
- **PDF parsing:** PyMuPDF (fitz).

## Quick start

```bash
# 1. Install deps (backend + frontend)
task study-app:install

# 2. Add your OpenRouter API key
cp study-app/backend/.env.example study-app/backend/.env
# edit study-app/backend/.env and set OPENROUTER_API_KEY  (from https://openrouter.ai/keys)

# 3. Add Clerk auth keys (login is required — the app is multi-user)
#    a. Create a dev app at https://dashboard.clerk.com
#    b. Enable Email/Password as a sign-in method
#    c. Copy the keys:
cp study-app/frontend/.env.example study-app/frontend/.env
#   - backend/.env:  CLERK_SECRET_KEY=sk_test_…
#   - frontend/.env: VITE_CLERK_PUBLISHABLE_KEY=pk_test_…

# 4. Run both servers
task study-app:dev
```

Then open http://localhost:5173 — you'll be asked to sign up before anything
else. Upgrading from the pre-auth single-user app? Delete
`study-app/backend/study_app.db` first (the multi-user schema resets the
database; uploads and study plans are regenerable).

## Auth & multi-user

Identity is delegated to [Clerk](https://clerk.com): the frontend renders
Clerk's hosted login/signup flows and attaches the session JWT to every API
call — as an `Authorization: Bearer` header for fetch/XHR/SSE, and as a
`?token=` query parameter where headers can't travel (`<img>` slide/file
URLs, `sendBeacon` telemetry). The backend verifies the JWT with the
official `clerk-backend-api` SDK (`app/auth.py`) and stamps the Clerk user
id on every row. All data — documents, content, memory blobs, mastery,
plans, event log — is owner-scoped; cross-user ids 404. Background jobs
(the proactive loop, reflection) iterate users. Tests bypass Clerk via a
`get_current_user` dependency override (`tests/conftest.py`).

## Project layout

```
study-app/
├── Dockerfile / fly.toml        # single-container deploy (API + built SPA)
├── supabase/                    # Supabase CLI config (production Postgres)
├── backend/
│   ├── pyproject.toml           # uv-managed deps
│   ├── .env.example             # OPENROUTER_API_KEY, Clerk, DATABASE_URL…
│   ├── alembic.ini
│   ├── app/
│   │   ├── main.py              # FastAPI app + lifespan
│   │   ├── config.py            # settings (pydantic-settings)
│   │   ├── db.py                # async engine (SQLite/Postgres) + Alembic init
│   │   ├── models.py            # 14 tables: content, memory, plans, concepts…
│   │   ├── auth.py              # Clerk JWT verification, user contextvar
│   │   ├── llm.py               # OpenRouter client (chat / chat_json)
│   │   ├── parsers.py           # PyMuPDF + LibreOffice → PDF
│   │   ├── transcription.py     # Qwen3-ASR via OpenRouter
│   │   ├── storage.py           # filesystem layer for uploads
│   │   ├── proactive.py         # background loop (flag-gated)
│   │   ├── routes/              # 15 routers: documents, generate (SSE),
│   │   │                        #   content, quiz, flashcards, modules, plans,
│   │   │                        #   lectures, study-session, recommend,
│   │   │                        #   concepts, analytics, activity, events, memory
│   │   ├── events/              # in-process event bus + handlers
│   │   ├── recommend/           # strategies, session, telemetry, LinUCB bandit
│   │   └── agent/
│   │       ├── graph.py         # LangGraph StateGraph + run_generation()
│   │       ├── state.py         # AgentState TypedDict
│   │       ├── nodes.py         # the 6 pipeline nodes
│   │       ├── tools.py         # feature-specific generation (notes/quiz/cards)
│   │       ├── memory.py        # read/write AgentMemory
│   │       └── fsrs_scheduler.py · planner.py · reflection.py ·
│   │           concept_graph.py · behavior.py · stats.py
│   ├── migrations/              # Alembic (baseline + pillars)
│   ├── scripts/sqlite_to_pg.py  # one-time SQLite → Postgres data copy
│   ├── evals/                   # DeepEval harness + datasets (own README)
│   └── tests/
└── frontend/
    ├── package.json
    ├── vite.config.ts           # proxies /api → :8000
    └── src/
        ├── router.tsx           # TanStack Router route tree
        ├── api/client.ts        # typed API wrapper
        ├── api/track.ts         # batched telemetry (sendBeacon)
        ├── analytics.ts         # Umami
        ├── auth.ts + components/Auth.tsx   # Clerk
        ├── types.ts             # mirrors backend schemas
        └── components/          # NotesView, QuizView, FlashcardView,
                                 #   ModulesPage, RecordPage, ConceptsPage, …
```

The full file-by-file map lives in `ARCHITECTURE.md`'s appendix.

## API

Highlights — there's a route module per feature area; `ARCHITECTURE.md` has
the full map.

| Method   | Path                                  | Purpose                                                  |
| -------- | ------------------------------------- | -------------------------------------------------------- |
| `POST`   | `/api/documents`                      | Upload a PDF/TXT/MD (multipart `file`)                   |
| `GET`    | `/api/documents`                      | List documents                                           |
| `GET`    | `/api/documents/{id}`                 | Get document + extracted text                            |
| `DELETE` | `/api/documents/{id}`                 | Delete a document                                        |
| `POST`   | `/api/generate`                       | Run the agent: `{document_id, task_type, instructions?}` (SSE stream) |
| `GET`    | `/api/content`                        | List generated content (filter by `document_id`, `type`) |
| `GET`    | `/api/content/{id}`                   | Get one content item                                     |
| `DELETE` | `/api/content/{id}`                   | Delete a content item                                    |
| `POST`   | `/api/quiz/{id}/attempt`              | Submit quiz answers → scored attempt                     |
| `POST`   | `/api/flashcards/{id}/review`         | Submit flashcard grades → FSRS + mastery update          |
| `GET`    | `/api/modules`                        | Semester tree of modules/lessons/documents               |
| `POST`   | `/api/modules`                        | Create a module (PATCH/DELETE on `/api/modules/{id}`, lessons on `/api/lessons/{id}`) |
| `GET/POST` | `/api/modules/{module_id}/plan`     | Get/generate the module's study plan                     |
| `PATCH`  | `/api/plans/{plan_id}/items/{item_id}` | Check off / update a plan item                         |
| `POST`   | `/api/lectures`                       | Lecture sessions (record → playback, slides + timestamps) |
| `POST`   | `/api/study-session`                  | Compose a mixed review/new session (`POST /{id}/review` to submit grades) |
| `GET`    | `/api/recommend`                      | Home-page recommendations (`POST /api/recommend/feedback` for interactions) |
| `GET`    | `/api/concepts`                       | Concept dashboard (recall, due/weak/mastered filters)    |
| `GET`    | `/api/concepts/graph`                 | Knowledge graph (concepts + edges); manual CRUD alongside |
| `GET`    | `/api/analytics/summary`              | Learning analytics: streak, study time, quiz stats, retention curve |
| `POST`   | `/api/activity`                       | Batched telemetry ingest (sendBeacon, 202)               |
| `GET`    | `/api/events`                         | `agent_events` audit ledger                              |
| `GET`    | `/api/memory`                         | Debug: inspect agent memory (POC transparency)           |
| `GET`    | `/health`                             | Health check                                             |

## Swapping models

OpenRouter serves hundreds of models under one API. Change `OPENROUTER_MODEL`
in `backend/.env`:

```
OPENROUTER_MODEL=anthropic/claude-sonnet-4
OPENROUTER_MODEL=openai/gpt-4o
OPENROUTER_MODEL=google/gemini-flash-1.5
OPENROUTER_MODEL=deepseek/deepseek-v4-flash-0731   # default — cheap + fast
```

No code changes needed.

## Deployment (study.inkpens.tech)

One Fly.io container serves both the API and the built SPA — single origin,
automatic TLS, a persistent volume at `/data` for SQLite + uploads.
Deploy config: `study-app/Dockerfile` + `study-app/fly.toml`; CI:
`.github/workflows/deploy-study.yml` (deploys on merge to main touching
`study-app/**`).

### One-time setup

1. **Fly**: `brew install flyctl && fly auth login`, then from `study-app/`:
   `fly apps create inkpens-study && fly volume create study_data --region lhr --size 1`
   and `fly secrets set OPENROUTER_API_KEY=… CLERK_SECRET_KEY=sk_live_…
   CLERK_AUTHORIZED_PARTIES='["https://study.inkpens.tech"]' PROACTIVE_ENABLED=true`.
2. **DNS** (registrar): add `study` CNAME → `inkpens-study.fly.dev`, then
   `fly certs add study.inkpens.tech` (Let's Encrypt issues automatically).
3. **Clerk production**: claim the app (`clerk auth login`), pull live keys
   (`clerk env pull --instance prod`), enable Email/Password **on the
   production instance** (sign-in methods are per-instance), and register
   `https://study.inkpens.tech` as a production domain. Set the repo
   variable `STUDY_CLERK_PUBLISHABLE_KEY` (pk_live_…) and the
   `study-app` GitHub environment secret `FLY_API_TOKEN`
   (`fly tokens create deploy -a inkpens-study`).
4. Deploy: `cd study-app && fly deploy --build-arg VITE_CLERK_PUBLISHABLE_KEY=pk_live_…`.

Cost lever: always-on is ~$4/month (`min_machines_running = 1` in fly.toml);
set it to 0 to park when idle at the cost of cold starts and delayed
background jobs.

## Database (SQLite → Postgres)

The default database is SQLite at `DB_PATH` — zero setup for dev and
tests. Set `DATABASE_URL` to any Postgres and the app switches dialects:
asyncpg driver, JSONB payloads, timezone-aware timestamps, and the
Postgres-only pillar tables (`document_chunks` + pgvector, with HNSW
index; RLS enabled on every table so Supabase's public REST API denies
anon access while the app's direct owner connection is unaffected).

Schema management is Alembic (`backend/migrations/`) — `init_db()`
upgrades to head on startup; legacy pre-alembic SQLite files are stamped
and migrated, not replayed. `uv run alembic revision --autogenerate -m …`
after model changes (run it against a fresh `DB_PATH` to get full-table
diffs).

### Supabase (free tier) in production

Free tier: 500 MB, pgvector, always-on while in use. Two footguns are
already handled by the workflows in `.github/`:

1. **7-day inactivity pause** — direct SQL connections don't reliably
   count as activity; `supabase-keepalive.yml` pings the REST gateway
   weekly.
2. **No automated backups on free** — `supabase-backup.yml` runs a weekly
   `pg_dump` to a 90-day workflow artifact.

Cutover (one-time):
1. Create a project (London region, next to the `lhr` app). Grab the
   **transaction pooler** string (port 6543 — the app) and the
   **session/direct** string (port 5432 — pg_dump).
2. `fly secrets set DATABASE_URL='postgres://…:6543/postgres'
   ACTIVITY_LEDGER_MAX_ROWS=0` — the ledger becomes an unpruned history.
3. Set the repo variable `SUPABASE_PROJECT_REF` and the secrets
   `SUPABASE_ANON_KEY`, `SUPABASE_DB_URL_DIRECT` to arm the two workflows.
4. Optional data copy from an existing SQLite file:
   `DATABASE_URL='postgres://…' uv run python scripts/sqlite_to_pg.py`.

CI runs the whole test suite against a real Postgres+pgvector service
container (PR checks and the deploy gate), so dialect drift can't reach
production unnoticed. Rollback: unset `DATABASE_URL` and redeploy — all
schema changes are additive.

## Out of scope (for now)

- The embeddings pipeline: the `document_chunks` + pgvector (HNSW) foundation
  exists on Postgres, but nothing fills it yet — semantic search / RAG over
  documents is a follow-up (the repo's `embeddings/` service is a natural
  integration point)
- Production UI polish
