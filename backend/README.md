# Project Aurora — Backend Service

The backend is an asynchronous, high-performance Python application built on **FastAPI** and **SQLite** (via `aiosqlite`). It serves as the central AI Gateway, orchestrates the multi-step agent tool loop, manages episodic and semantic memory, and computes diurnal and weekly agenda horizons.

---

## Architecture Overview

```
backend/app/
├── main.py                     # Application startup, CORS configuration, router mounting
├── core/
│   └── config.py               # Pydantic Settings (.env resolution, LLM keys, DB path)
├── api/v1/
│   ├── router.py               # Master API router mounting all domain routers
│   └── endpoints/              # Chat, Agenda, Recovery, Memories, Tasks, Auth, etc.
└── services/
    ├── ai/                     # Multi-provider AI Gateway (Gemini, OpenAI, Mock)
    ├── conversation/           # Agent loop, multi-step tool recursion, session store
    ├── tools/                  # Tool Registry, Executor, AST Safe Calculator, 20+ built-ins
    ├── context/                # Dynamic context router & privacy policy engine
    ├── memory/                 # SQLite memory stores, candidate extraction, retrieval
    ├── agenda/                 # Diurnal horizon, weekly horizon, briefing, recovery
    ├── tasks/                  # Task storage & lifecycle management
    ├── commitments/            # External commitment tracking
    ├── reminders/              # Timed notification reminders
    ├── calendar/ & contacts/   # Ingestion managers for client calendar & contact data
    ├── location/ & assets/     # Contextual location updates & ephemeral asset TTL engine
    ├── identity/               # Cryptographic token provisioning & auth dependencies
    └── web/                    # SSRF-protected web fetcher, parser & citations
```

---

## Quickstart

### 1. Requirements
* Python 3.11+ (Python 3.12 or 3.14 recommended)
* `pip` or `uv`

### 2. Environment Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration (`.env`)

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Set your preferred AI Provider and API key:

```env
# Google Gemini (Default)
AI_PROVIDER=gemini
AI_MODEL=gemini-3.8-flash
GEMINI_API_KEY=your_gemini_api_key

# Or OpenAI
# AI_PROVIDER=openai
# AI_MODEL=gpt-4o-mini
# OPENAI_API_KEY=your_openai_api_key

# Server settings
HOST=0.0.0.0
PORT=8000
DEBUG=True
DATABASE_PATH=data/aurora.db
```

### 4. Running the Dev Server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

* **Swagger API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
* **Health Check**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

---

## Running Automated Tests

The backend includes a comprehensive pytest suite covering all components with 1,330+ tests:

```bash
# Run all tests
backend/.venv/bin/pytest backend/tests -v

# Run tests by domain
backend/.venv/bin/pytest backend/tests/test_tool_loop_and_confirmation.py -v
backend/.venv/bin/pytest backend/tests/test_phase6i_schedule_recovery.py -v
backend/.venv/bin/pytest backend/tests/test_phase5l1_candidate_extraction.py -v
backend/.venv/bin/pytest backend/tests/test_phase5k1_security.py -v
```

---

## Security Architecture

* **AST-evaluated Math**: The `calculate` tool uses Python `ast` parsing to restrict operations strictly to arithmetic. Arbitrary code execution or `eval()` is strictly prohibited.
* **SSRF Guard**: The web client blocks loopback and private RFC-1918 subnets (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`).
* **Multi-User Isolation**: Every request is authenticated against provisioned credentials and scoped to `user_id`.
* **Ephemeral Asset TTL**: Ingested binary attachments and photos are kept in memory with a hard 3600-second TTL before being expunged.
