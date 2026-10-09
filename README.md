# Project Aurora — Personal AI Operating Layer

> **Note**: *Project Aurora* is a neutral working name used throughout the codebase during development. No permanent brand or product names are hardcoded.

Project Aurora is an architecture for a proactive, context-aware personal AI operating layer. Rather than a simple chatbot, Aurora is designed to integrate with personal workflows, maintain context and memory, utilize external tools with safety boundaries, and proactively assist the user.

---

## Phase 3 Architecture: Tool System & Action Architecture

In Phase 3, Aurora transitions from a conversational model into an action-capable assistant with safe, structured tool calling and mandatory human-in-the-loop authorization for sensitive actions:

```text
                                  ┌────────────────────────────────┐
                                  │      Client (iOS / SwiftUI)    │
                                  │   Conversation + Confirm Card  │
                                  └───────────────┬────────────────┘
                                                  │ HTTPS / REST
                                  ┌───────────────▼────────────────┐
                                  │        API Gateway Layer       │
                                  │   POST /api/v1/chat            │
                                  │   POST /api/v1/chat/confirm    │
                                  │   GET  /api/v1/tools           │
                                  └───────────────┬────────────────┘
                                                  │
                                  ┌───────────────▼────────────────┐
                                  │      Conversation Engine       │
                                  │   (Tool Loop, Session Store,   │
                                  │    Context Window Manager)     │
                                  └───────┬───────────────┬────────┘
                                          │               │
                 ┌────────────────────────▼──┐         ┌──▼────────────────────────┐
                 │       Tool Registry       │         │       Tool Executor       │
                 │ ┌───────────────────────┐ │         │ ┌───────────────────────┐ │
                 │ │ get_current_time (LOW)│ │         │ │ Argument Validation   │ │
                 │ │ calculate (LOW, safe) │ │         │ │ Permission Checking   │ │
                 │ │ reminder (MED, conf)  │ │         │ │ Confirmation Intercept│ │
                 │ └───────────────────────┘ │         │ └───────────────────────┘ │
                 └───────────────────────────┘         └───────────────────────────┘
                                          │
                                  ┌───────▼────────────────────────┐
                                  │         AI Gateway             │
                                  │  (OpenAI / Mock Providers)     │
                                  └────────────────────────────────┘
```

---

## Key Features in Phase 3

### 1. Standardized Tool System (`backend/app/services/tools/`)
* **`base.py` (`BaseTool`, `ToolDefinition`, `ToolRiskLevel`)**:
  - Exposes metadata: name, description, JSON schema parameters, risk level (`LOW`, `MEDIUM`, `HIGH`), and confirmation flags.
* **`registry.py` (`ToolRegistry`)**:
  - Dynamic registration, unregistration, lookup, and provider-agnostic schema export.
* **`executor.py` (`ToolExecutor`)**:
  - Validates input arguments against schema before execution.
  - Enforces confirmation requirement: if `requires_confirmation=True` or `risk_level == HIGH`, raises `ToolConfirmationRequired` and pauses execution.
  - Safely captures execution errors without crashing the server.

### 2. Built-in Deterministic Tools (`backend/app/services/tools/builtins/`)
* **`get_current_time`**:
  - Risk: `LOW`. Confirmation: `NO`.
  - Supports IANA timezone lookups (e.g. `UTC`, `America/New_York`, `Asia/Kolkata`).
* **`calculate`**:
  - Risk: `LOW`. Confirmation: `NO`.
  - **Security**: Evaluated via safe Python `ast` syntax tree walk. Whitelists arithmetic operators (`+`, `-`, `*`, `/`, `**`). **Never executes unsafe `eval()`, shell commands, or arbitrary code.**
* **`create_test_reminder`**:
  - Risk: `MEDIUM`. Confirmation: **`YES` (Mandatory)**.
  - Requires explicit user approval before execution.

### 3. Human-in-the-Loop Confirmation Flow
1. AI requests a confirmation-required tool (e.g. `create_test_reminder`).
2. Backend pauses execution and returns `{"type": "confirmation_required", "confirmation_id": "...", "tool": {...}}`.
3. Client displays an Action Authorization card to the user.
4. User selects **Approve** or **Reject**.
5. Client posts to `/api/v1/chat/confirm`:
   - If approved: tool executes and AI synthesizes the final answer.
   - If rejected: tool execution is cancelled and AI acknowledges the cancellation.

### 4. iOS SwiftUI Action UI
* Interactive authorization card rendered directly in the chat stream:
  - Tool name and description.
  - Risk level badge (`MEDIUM RISK` / `HIGH RISK`).
  - Formatted argument inspection.
  - **Approve Action** (green) and **Reject** (red) buttons.

---

## Quickstart Guide

### 1. Running the Backend

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Endpoints:
* **Health Check**: `GET http://127.0.0.1:8000/api/v1/health`
* **List Tools**: `GET http://127.0.0.1:8000/api/v1/tools`
* **Chat**: `POST http://127.0.0.1:8000/api/v1/chat`
* **Confirm Action**: `POST http://127.0.0.1:8000/api/v1/chat/confirm`

---

### 2. Running Automated Tests

Run the full pytest suite (31 tests covering Registry, Executor, Security, Tool Loop, Confirmations, and Regression):
```bash
PYTHONPATH=backend pytest backend/tests -v
```

---

### 3. Running the iOS Client

1. Open `ios/ProjectAurora.xcodeproj` in Xcode.
2. Run on any iOS 17+ Simulator.
3. Try asking:
   - *"What time is it in Tokyo?"* (calls `get_current_time` automatically).
   - *"Calculate 25 * 4 + 10"* (calls `calculate` automatically).
   - *"Remind me to call Rahul tomorrow at 9 AM"* (triggers the confirmation card with Approve/Reject buttons).
