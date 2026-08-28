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
│                 │        │  SQLite + filesystem                     │
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

- **Backend:** Python 3.13, FastAPI, Pydantic, SQLAlchemy 2 (async, SQLite),
  LangGraph, OpenAI SDK (pointed at OpenRouter). Managed with `uv`.
- **Frontend:** React 19, TypeScript, Vite, TanStack Query, React Router,
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
├── backend/
│   ├── pyproject.toml          # uv-managed deps
│   ├── .env.example            # OPENROUTER_API_KEY, OPENROUTER_MODEL
│   ├── app/
│   │   ├── main.py             # FastAPI app + lifespan
│   │   ├── config.py           # settings (pydantic-settings)
│   │   ├── db.py               # async SQLAlchemy engine/session
│   │   ├── models.py           # Document, ContentItem, QuizAttempt, AgentMemory
│   │   ├── schemas.py          # Pydantic request/response models
│   │   ├── llm.py              # OpenRouter client (chat / chat_json)
│   │   ├── parsers.py          # PyMuPDF text extraction
│   │   ├── storage.py          # filesystem layer for uploads
│   │   ├── routes/             # documents, generate, content, quiz, memory
│   │   └── agent/
│   │       ├── graph.py        # LangGraph StateGraph + run_generation()
│   │       ├── state.py        # AgentState TypedDict
│   │       ├── nodes.py        # the 6 pipeline nodes
│   │       ├── tools.py        # feature-specific generation (notes/quiz/cards)
│   │       └── memory.py       # read/write AgentMemory
│   └── tests/
└── frontend/
    ├── package.json
    ├── vite.config.ts          # proxies /api → :8000
    └── src/
        ├── App.tsx             # layout, tabs, generate flow
        ├── api/client.ts       # typed API wrapper
        ├── types.ts            # mirrors backend schemas
        └── components/         # Sidebar, NotesView, QuizView, FlashcardView
```

## API

| Method   | Path                     | Purpose                                                  |
| -------- | ------------------------ | -------------------------------------------------------- |
| `POST`   | `/api/documents`         | Upload a PDF/TXT/MD (multipart `file`)                   |
| `GET`    | `/api/documents`         | List documents                                           |
| `GET`    | `/api/documents/{id}`    | Get document + extracted text                            |
| `DELETE` | `/api/documents/{id}`    | Delete a document                                        |
| `POST`   | `/api/generate`          | Run the agent: `{document_id, task_type, instructions?}` |
| `GET`    | `/api/content`           | List generated content (filter by `document_id`, `type`) |
| `GET`    | `/api/content/{id}`      | Get one content item                                     |
| `DELETE` | `/api/content/{id}`      | Delete a content item                                    |
| `POST`   | `/api/quiz/{id}/attempt` | Submit quiz answers → scored attempt                     |
| `GET`    | `/api/memory`            | Debug: inspect agent memory (POC transparency)           |
| `GET`    | `/health`                | Health check                                             |

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

## Out of scope (for now)

- Streaming agent steps (could add SSE for an "agent is thinking…" UX)
- Semantic search / RAG over documents (structured memory only for v1; the
  repo's `embeddings/` service is a natural future integration point)
- Production UI polish
