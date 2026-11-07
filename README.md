# Project Aurora (Pankii OS) — Personal AI Operating Layer

> **Working Title**: *Project Aurora* (Pankii OS) is a privacy-first, context-aware personal AI operating layer and intelligent daily executive. Rather than functioning as a passive conversational chatbot, Aurora integrates deeply with local system data, coordinates daily horizons and schedules, extracts long-term semantic memory, and executes real-world actions with mandatory human-in-the-loop safeguards.

---

## Architecture & System Overview

Aurora consists of a high-performance **Python / FastAPI** core backend paired with a native **iOS (SwiftUI / App Intents)** client. The platform bridges local device context (Calendar, Contacts, CoreLocation, Reminders, Ephemeral Photos) with multi-provider generative AI intelligence (Google Gemini and OpenAI), protected by an action confirmation interceptor and dynamic context routers.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   NATIVE CLIENT (iOS 17+)                              │
│                                                                                        │
│   ┌────────────────────┐    ┌────────────────────┐    ┌────────────────────────────┐   │
│   │ SwiftUI Assistant  │    │ Voice Interaction  │    │   Siri & App Shortcuts     │   │
│   │ • Multi-turn Chat  │    │ • SFSpeechCapture  │    │ • OpenAuroraVoiceIntent    │   │
│   │ • Confirm Cards    │    │ • AVSpeechOutput   │    │ • OpenAgendaIntent         │   │
│   │ • Agent Stepper    │    │ • Voice Confirm    │    │ • AskAuroraIntent          │   │
│   └─────────┬──────────┘    └─────────┬──────────┘    └──────────────┬─────────────┘   │
│             │                         │                              │                 │
│   ┌─────────▼─────────────────────────▼──────────────────────────────▼─────────────┐   │
│   │                      Device Services & Context Providers                       │   │
│   │   EventKit (Calendar) │ ContactsKit │ CoreLocation │ PhotoKit │ Keychain Store │   │
│   └───────────────────────────────────┬────────────────────────────────────────────┘   │
└───────────────────────────────────────┼────────────────────────────────────────────────┘
                                        │ HTTPS / REST API (/api/v1)
                                        │ Token Auth (Bearer / Keychain)
┌───────────────────────────────────────▼────────────────────────────────────────────────┐
│                               FASTAPI BACKEND CORE                                     │
│                                                                                        │
│   ┌────────────────────────────────────────────────────────────────────────────────┐   │
│   │ API Routing Layer (/api/v1)                                                    │   │
│   │ /auth • /chat • /agenda • /schedule/recovery • /memories • /tasks              │   │
│   │ /commitments • /reminders • /calendar • /contacts • /location • /assets        │   │
│   └───────────────────────────────────┬────────────────────────────────────────────┘   │
│                                       │                                                │
│   ┌───────────────────────────────────▼────────────────────────────────────────────┐   │
│   │ Conversation & Multi-Step Agent Execution Engine                               │   │
│   │ • Iterative Tool Execution Loop        • Context Window Management             │   │
│   │ • Human-in-the-Loop Interceptor        • Error Recovery & Graceful Fallbacks   │   │
│   └───────┬───────────────────────────┬────────────────────────────┬───────────────┘   │
│           │                           │                            │                   │
│   ┌───────▼─────────────┐     ┌───────▼─────────────┐      ┌───────▼───────────────┐   │
│   │   Context Engine    │     │  Memory Subsystem   │      │   Agenda & Horizons   │   │
│   │ • Dynamic Policies  │     │ • Candidate Extr.   │      │ • Diurnal Horizon     │   │
│   │ • Location Snapshot │     │ • Human Review UI   │      │ • Weekly Horizon      │   │
│   │ • Calendar Snapshot │     │ • Semantic Storage  │      │ • Event Briefing      │   │
│   │ • Ephemeral Assets  │     │ • Proactive Recall  │      │ • Schedule Recovery   │   │
│   └─────────────────────┘     └─────────────────────┘      └───────────────────────┘   │
│           │                           │                            │                   │
│   ┌───────▼───────────────────────────▼────────────────────────────▼───────────────┐   │
│   │ Tool Ecosystem (Registry & Executor)                                           │   │
│   │ • Deterministic Built-ins: calculate (AST safe), get_current_time              │   │
│   │ • System Tools: tasks, commitments, reminders, calendar, contacts, assets      │   │
│   │ • Time & Agenda Tools: horizon, briefing, recovery, coordination               │   │
│   │ • Web Intelligence: SSRF-hardened reader, search, citations                   │   │
│   └───────────────────────────────────┬────────────────────────────────────────────┘   │
│                                       │                                                │
│   ┌───────────────────────────────────▼────────────────────────────────────────────┐   │
│   │ AI Provider Abstraction Gateway                                                │   │
│   │ • Google Gemini (gemini-3.8-flash via REST v1beta)                             │   │
│   │ • OpenAI (gpt-4o / gpt-4o-mini via REST v1)                                    │   │
│   │ • Deterministic Mock Provider (unit testing & offline development)             │   │
│   └───────────────────────────────────┬────────────────────────────────────────────┘   │
│                                       │                                                │
│   ┌───────────────────────────────────▼────────────────────────────────────────────┐   │
│   │ SQLite Storage Engine (WAL mode, async via aiosqlite)                          │   │
│   │ • Tasks • Commitments • Reminders • Memories • Credentials • Horizons          │   │
│   └────────────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Capabilities & Subsystems

### 1. Autonomous Multi-Step Agent Loop
* **Recursive Reason-Act-Observe Loop**: Aurora dynamically determines when external tools are required, generates argument calls, executes them through the secure registry, observes the result, and synthesizes the next thought until arriving at a final response.
* **Bounded Execution Guardrails**: Enforces hard limits on maximum tool recursion depth to prevent infinite loops, catching tool execution errors without terminating the conversation session.
* **Activity Stream**: Emits real-time agent execution status indicators to the iOS client so users see which tools the model is inspecting in flight.

### 2. Human-in-the-Loop Action Architecture
* **Risk Categorization**:
  * `LOW`: Read-only queries, safe calculations, non-destructive status checks. Executes autonomously.
  * `MEDIUM`: Creation of local reminders, non-destructive task modifications. Requires explicit confirmation if configured.
  * `HIGH`: Destructive updates, event cancellations, applying schedule recovery plan modifications, external communications. **Mandatory user authorization required**.
* **Pause & Resume Workflow**:
  1. The agent selects a tool requiring confirmation.
  2. The backend halts execution and returns a structured `confirmation_required` payload with a unique cryptographic `confirmation_id` and the proposed arguments.
  3. The iOS client displays an **Action Authorization Card** in the chat stream (or prompts via voice).
  4. The user taps **Approve** or **Reject** (or responds affirmatively via voice).
  5. The client posts the decision to `/api/v1/chat/confirm`, resuming the agent loop to conclude the action.

### 3. Diurnal & Weekly Horizon Intelligence
* **Diurnal Horizon (Daily Executive)**:
  * Generates an end-to-end plan for the day combining calendar events, due tasks, pending commitments, and proactive suggestions.
  * Facilitates morning briefings and evening retrospectives.
* **Weekly Horizon**:
  * Evaluates multi-day balance, analyzes schedule density across the week, and flags impending commitment deadlines.
* **Event Intelligence & Briefings**:
  * Automatically synthesizes pre-meeting briefs: attendees, past relationship context from contacts, open commitments with participants, and suggested talking points.
  * Generates post-meeting debrief sheets to record action items and extract follow-up commitments.
* **Schedule Disruption & Recovery Engine**:
  * Continuously evaluates timeline collisions, travel transit constraints, and missed tasks.
  * Generates intelligent recovery proposals (e.g., auto-shifting meetings, inserting decompression buffers, or moving low-priority tasks) with one-tap user execution.

### 4. Long-Term Memory & Candidate Extraction
* **Episodic & Semantic Memory**: Stores user preferences, project context, personal facts, and relationships in SQLite with indexed semantic tags.
* **Candidate Extraction Pipeline**: The conversation engine automatically identifies potential durable facts and stages them as **Memory Candidates**.
* **Candidate Review UI**: Users review candidate memories via an interactive review sheet in iOS to confirm, modify, or reject them before committing to permanent storage.
* **Context Injection**: Relevant memories are proactively retrieved and injected into the active prompt window based on conversational relevance.

### 5. Native iOS Voice & Hands-Free Interaction
* **Speech-to-Text & Text-to-Speech**: Integrated `SFSpeechRecognizer` for on-device/low-latency transcription and `AVSpeechSynthesizer` for natural voice feedback.
* **`VoiceSessionCoordinator`**: Manages audio session interruptions, microphone permissions, silence timeouts, and turn-taking.
* **Voice Action Interpretation**: `VoiceConfirmationInterpreter` parses natural voice responses (e.g., *"Yes, please proceed"*, *"Confirm that"*, *"Cancel"*) to approve or dismiss sensitive tool executions completely hands-free.
* **Siri & App Shortcuts**: Native App Intents allow instant voice triggers from anywhere in iOS:
  * `OpenAuroraVoiceIntent`: Activates voice conversation mode immediately.
  * `OpenAgendaIntent`: Deep-links straight to the daily timeline.
  * `AskAuroraIntent`: Headless background natural language queries with foreground handoff for confirmations.

### 6. System Context Providers & Ephemeral Assets
* **Local System Integration**:
  * **Calendar**: EventKit bridge querying upcoming schedules and conflict points.
  * **Contacts**: Apple Contacts integration resolving participant relationships and communication channels.
  * **Location**: CoreLocation updates providing reverse-geocoded spatial awareness with user privacy controls.
* **Ephemeral Asset Pipeline**:
  * Ingests photos, documents, and attachments with strict privacy controls.
  * Enforces a hard **3600-second TTL** boundary; expired assets are swept from memory immediately.
  * Exposes only metadata to LLMs without leaking raw binary payloads.

### 7. Safe Web Reader & Citation System
* **SSRF Protection**: Hardened web client blocks private IP ranges, loopback addresses (`127.0.0.1`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.0.0/16`), and non-standard schemes.
* **Clean Text Extraction**: Strips tracker scripts, CSS, and boilerplate to ingest readable text directly into agent context with verifiable source citations.

---

## Tool Ecosystem Reference

The platform includes over 20 built-in, type-safe deterministic tools registered in the backend (`backend/app/services/tools/builtins/`):

| Tool Name | Domain | Risk Level | Confirmation Required | Description |
|:---|:---|:---:|:---:|:---|
| `get_current_time` | System | `LOW` | No | Retrieves current time in any standard IANA timezone. |
| `calculate` | Math | `LOW` | No | Safe AST-evaluated arithmetic calculator (no `eval()` or shell access). |
| `get_agenda_timeline` | Agenda | `LOW` | No | Fetches chronological schedule combining calendar, tasks, and commitments. |
| `get_diurnal_horizon` | Agenda | `LOW` | No | Retrieves morning briefing, evening retrospective, and day health metrics. |
| `get_weekly_horizon` | Agenda | `LOW` | No | Analyzes weekly workload distribution and upcoming deadlines. |
| `get_event_briefing` | Briefing | `LOW` | No | Compiles attendee dossiers, relevant context, and agenda for a calendar event. |
| `detect_schedule_disruptions`| Agenda | `LOW` | No | Inspects calendar for overlaps, transit conflicts, and buffer violations. |
| `propose_schedule_recovery` | Recovery | `LOW` | No | Formulates intelligent rescheduling proposals to resolve conflicts. |
| `apply_schedule_recovery` | Recovery | `HIGH` | **Yes (Mandatory)** | Modifies calendar events and commitments based on approved recovery plan. |
| `propose_coordination_slots`| Coordination| `LOW` | No | Identifies optimal meeting times across participants. |
| `list_tasks` | Tasks | `LOW` | No | Queries tasks filtered by status, priority, or project tag. |
| `create_task` | Tasks | `MEDIUM`| Configurable | Creates a new structured task with due dates and priorities. |
| `update_task` | Tasks | `MEDIUM`| Configurable | Modifies status or properties of an existing task. |
| `list_commitments` | Commitments | `LOW` | No | Tracks active promises made to or by external contacts. |
| `create_commitment` | Commitments | `MEDIUM`| Configurable | Logs an external commitment with deadlines and counter-parties. |
| `list_reminders` | Reminders | `LOW` | No | Retrieves active system reminders. |
| `create_reminder` | Reminders | `MEDIUM`| **Yes** | Creates a timed notification reminder. |
| `get_calendar_events` | Calendar | `LOW` | No | Queries local calendar events across a date range. |
| `search_contacts` | Contacts | `LOW` | No | Looks up contact information and organizational relationships. |
| `get_current_location` | Location | `LOW` | No | Retrieves latest reverse-geocoded spatial location. |
| `inspect_asset_metadata` | Assets | `LOW` | No | Inspects metadata of actively ingested photos/files within TTL boundary. |
| `search_memories` | Memory | `LOW` | No | Queries episodic and semantic memories by topic or semantic query. |
| `store_memory` | Memory | `MEDIUM`| Configurable | Directly records a verified user fact or preference. |
| `list_proactive_candidates` | Proactive | `LOW` | No | Checks proactive recommendation feed. |
| `web_read` / `web_search` | Web | `LOW` | No | Fetches sanitized webpage text with SSRF validation. |

---

## API Endpoints Reference

All endpoints are versioned under `/api/v1` and documented via OpenAPI Swagger at `http://localhost:8000/docs`:

| Group | Method | Endpoint | Description |
|:---|:---:|:---|:---|
| **Auth** | `POST` | `/api/v1/auth/provision` | Provision user and device installation credentials. |
| | `POST` | `/api/v1/auth/dev-bootstrap` | Development bootstrap endpoint for automated client testing. |
| **Health** | `GET` | `/api/v1/health` | Service health status, DB connectivity, and active AI provider info. |
| **Chat & Tools**| `POST` | `/api/v1/chat` | Main conversational agent endpoint. Supports streaming and tool loops. |
| | `POST` | `/api/v1/chat/confirm` | Approves or rejects a paused human-in-the-loop action. |
| | `GET` | `/api/v1/tools` | Discovers all available registered tools and JSON schemas. |
| **Agenda** | `GET` | `/api/v1/agenda/timeline` | Unified chronological daily agenda. |
| | `GET` | `/api/v1/agenda/horizon` | Diurnal horizon (morning plan & evening review). |
| | `GET` | `/api/v1/agenda/weekly-horizon` | Multi-day horizon & weekly balance analytics. |
| | `GET` | `/api/v1/agenda/briefing/{event_id}` | Generates pre-event contextual briefing sheet. |
| | `GET` | `/api/v1/agenda/disruptions` | Evaluates schedule disruptions and transit violations. |
| | `POST` | `/api/v1/agenda/coordination` | Computes candidate meeting windows for participants. |
| **Schedule** | `POST` | `/api/v1/schedule/recovery/proposal` | Formulates recovery plan for current schedule conflicts. |
| | `POST` | `/api/v1/schedule/recovery/apply` | Applies user-authorized schedule modifications. |
| **Memories** | `GET` / `POST`| `/api/v1/memories` | List, query, or manually insert long-term memories. |
| | `GET` | `/api/v1/memories/candidates` | Fetches extracted memory candidates awaiting user review. |
| | `POST` | `/api/v1/memories/candidates/{id}/review` | Promotes candidate to long-term memory or discards it. |
| **Tasks** | `GET` / `POST`| `/api/v1/tasks` | CRUD management for personal tasks and priorities. |
| **Commitments**| `GET` / `POST`| `/api/v1/commitments` | Tracks external commitments, deadlines, and status. |
| **Reminders** | `GET` / `POST`| `/api/v1/reminders` | Read and schedule system reminders. |
| **Context** | `POST` | `/api/v1/calendar/sync` | Ingests snapshot of device calendar events. |
| | `POST` | `/api/v1/contacts/sync` | Ingests device contacts snapshot for relationship resolution. |
| | `POST` | `/api/v1/location/update` | Updates contextual device location. |
| | `POST` | `/api/v1/assets/ingest` | Ephemeral media ingestion (enforces 3600-second TTL). |
| **Proactive** | `GET` | `/api/v1/proactive/candidates` | Fetches proactive suggestions bounded to opt-in rules. |

---

## Codebase Structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                     # FastAPI application entrypoint & middleware
│   │   ├── core/
│   │   │   └── config.py               # Settings, environment configuration, LLM parameters
│   │   ├── api/v1/
│   │   │   ├── router.py               # Master API router mounting all domain endpoints
│   │   │   └── endpoints/              # Chat, Agenda, Recovery, Memories, Tasks, Auth, etc.
│   │   └── services/
│   │       ├── ai/                     # LLM Provider Gateway (Gemini, OpenAI, Mock)
│   │       ├── conversation/           # Agent loop, session manager, prompt synthesis
│   │       ├── context/                # Dynamic context engine, privacy routers, providers
│   │       ├── tools/                  # Tool registry, executor, AST math, and 20+ built-ins
│   │       ├── memory/                 # SQLite memory store, candidate extractor, retriever
│   │       ├── agenda/                 # Diurnal & weekly horizons, briefing, recovery
│   │       ├── tasks/ & commitments/   # Task and commitment stores & managers
│   │       ├── location/ & calendar/   # Device context data ingestion & sync managers
│   │       ├── contacts/ & assets/     # Contact relationship resolution & ephemeral asset TTL
│   │       ├── identity/               # Token provisioning and user credential validation
│   │       └── web/                    # SSRF-safe web reader, scraper, and citation engine
│   ├── tests/                          # 1,338+ passing pytest tests covering all subsystems
│   ├── requirements.txt                # Python dependencies (FastAPI, uvicorn, pydantic, etc.)
│   └── data/                           # Local SQLite database (aurora.db)
│
├── ios/
│   ├── ProjectAurora.xcodeproj         # Xcode Project (iOS 17+ target)
│   ├── ProjectAurora/
│   │   ├── ProjectAuroraApp.swift      # SwiftUI app lifecycle entrypoint
│   │   ├── Config/                     # AppConfig (API base URLs for Simulator / LAN)
│   │   ├── Models/                     # Swift Codable domain models matching backend schemas
│   │   ├── ViewModels/                 # Assistant, Agenda, Memory, Recovery, Voice ViewModels
│   │   ├── Views/                      # SwiftUI Views: Chat, Agenda Timeline, Briefings, Sheets
│   │   ├── Services/                   # AuroraAPIClient, VoiceSessionCoordinator, Keychain Store,
│   │   │                               # EventKit, Contacts, Location, Photo, Notification Providers
│   │   └── Intents/                    # Siri & App Shortcuts (OpenAuroraVoice, AskAurora)
│   └── ProjectAuroraTests/             # Swift XCTest suite (Voice, Horizons, Recovery, Integration)
```

---

## Getting Started

### Prerequisites
* **Python**: `3.11+` (Python 3.12 or 3.14 recommended)
* **macOS / iOS Development**: macOS Sonoma or later with **Xcode 15+** (iOS 17+ SDK)
* **AI Provider API Key**: A valid [Google Gemini API Key](https://aistudio.google.com/) (recommended) or [OpenAI API Key](https://platform.openai.com/).

---

### 1. Backend Setup & Run

1. Navigate to the backend directory:
   ```bash
   cd backend
   ```

2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables:
   Create a `.env` file in `backend/` (or set environment variables in your shell):
   ```env
   # AI Provider Configuration ("gemini" or "openai" or "mock")
   AI_PROVIDER=gemini
   AI_MODEL=gemini-3.8-flash
   GEMINI_API_KEY=your_gemini_api_key_here

   # If using OpenAI:
   # AI_PROVIDER=openai
   # AI_MODEL=gpt-4o-mini
   # OPENAI_API_KEY=your_openai_api_key_here

   # Server Settings
   HOST=0.0.0.0
   PORT=8000
   DEBUG=True
   DATABASE_PATH=data/aurora.db
   ```

5. Launch the FastAPI server:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

6. Verify the server is running:
   * **Root Status**: `http://localhost:8000/`
   * **Health Check**: `http://localhost:8000/api/v1/health`
   * **Interactive Swagger Documentation**: `http://localhost:8000/docs`

---

### 2. Running Automated Tests

The backend includes a comprehensive test suite of **1,338+ automated pytest tests** verifying everything from AST arithmetic security and SSRF web reader protection to multi-step tool loops, horizon orchestration, and schedule recovery lifecycles:

```bash
# From workspace root:
backend/.venv/bin/pytest backend/tests -v

# Or run specific subsystem tests:
backend/.venv/bin/pytest backend/tests/test_phase6i_schedule_recovery.py -v
backend/.venv/bin/pytest backend/tests/test_tool_loop_and_confirmation.py -v
backend/.venv/bin/pytest backend/tests/test_phase5k1_security.py -v
```

---

### 3. iOS Client Setup & Run

1. Open `ios/ProjectAurora.xcodeproj` in Xcode.
2. **Network Configuration**:
   * Inspect `ios/ProjectAurora/Config/AppConfig.swift`.
   * When running on the **iOS Simulator**, the client connects automatically to `http://127.0.0.1:8000`.
   * When running on a **physical iPhone**, set your host Mac's local LAN IP (e.g. `http://192.168.1.X:8000`) and ensure both devices are on the same Wi-Fi network.
3. Select an **iOS 17+ Simulator** (e.g. iPhone 15 Pro) or a connected physical device.
4. Press **Cmd + R** to build and run.
5. On first launch, the app connects to `/api/v1/auth/dev-bootstrap` or `/api/v1/auth/provision` and saves installation tokens securely in the Apple Keychain.

---

## Security, Safety & Privacy Guarantees

* **Mandatory Human Approval**: Actions modifying calendars, deleting tasks, or committing to schedule shifts cannot execute silently. The agent is forced to pause and present an authorization card.
* **Safe AST Math Execution**: The `calculate` tool walks a sanitized Python abstract syntax tree (`ast`) allowing only standard arithmetic operators. It never executes `eval()`, arbitrary code, or shell commands.
* **SSRF-Proof Web Scraping**: The web reader rejects internal loopback, link-local, and private RFC-1918 subnets before dispatching HTTP requests.
* **Ephemeral Asset Storage**: Uploaded photos and document buffers live in volatile memory with a strict **3600-second TTL**. Expired assets are automatically wiped and cannot be recovered.
* **Strict Multi-User Isolation**: Every database query and context snapshot is scoped to the authenticated `user_id`. Cross-user data access attempts return 404 or 403.
* **Local Keychain Credential Storage**: Client tokens are kept exclusively in iOS Keychain, avoiding plaintext credential exposure.

---

## License

Internal proprietary research & engineering codebase. All rights reserved. Neutral working nomenclature is maintained throughout the project.
