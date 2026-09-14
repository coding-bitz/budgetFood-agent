# BudgetFoodAgent — System Architecture

This document describes the architectural layout, component boundaries, execution flows, and system invariants of BudgetFoodAgent.

---

## 1. System Architecture Diagram

```
+---------------------------------------------------------------------------------------+
|                                    Client Tier                                        |
|                                                                                       |
|   +--------------------------+               +------------------------------------+   |
|   | Amazon Cognito           | <===========> | Evaluator Web Frontend             |   |
|   | User Pool & Client       |  JWT Token    | (HTML5 / Vanilla JS / Cognito SDK) |   |
|   +--------------------------+               +------------------+-----------------+   |
+-----------------------------------------------------------------|---------------------+
                                                                  |
                                       HTTPS / HTTP (Port 8080)   | Authorization: Bearer <JWT>
                                                                  v
+---------------------------------------------------------------------------------------+
|                                  Amazon EC2 Instance                                  |
|                                                                                       |
|   +-------------------------------------------------------------------------------+   |
|   | Gunicorn WSGI Server (2 Workers, Timeout: 300s)                               |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   | Flask Application (`app.py`)                                                  |   |
|   |   - GET /: Returns frontend/index.html with server-injected Cognito config    |   |
|   |   - POST /chat: Validates Cognito JWT via JWKS, extracts 'sub' as session_id  |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   | Strands Agent (`agent/orchestrator.py`)                                       |   |
|   | Model: VertexGeminiModel (`agent/model.py` via google-genai)                  |   |
|   |                                                                               |   |
|   |   +-----------------------------------+   +-------------------------------+   |   |
|   |   | SearchLimitsHook                  |   | LLMSteeringHandler            |   |   |
|   |   | Enforces batch size, radius steps |   | Checks final response against |   |   |
|   |   | and 6-batch safety limits         |   | actual tool ledger results    |   |   |
|   |   +-----------------------------------+   +-------------------------------+   |   |
|   +----+--------------------+--------------------+-----------------------+----+   |
|        |                    |                    |                       |        |
+--------|--------------------|--------------------|-----------------------|--------+
         |                    |                    |                       |
         v                    v                    v                       v
+------------------+ +------------------+ +--------------------+ +--------------------+
| S3 Session Store | | DynamoDB Tables  | | Google Maps APIs   | | Web Menu Scraping  |
|                  | |                  | |                    | |                    |
| S3SessionManager | | Recommendations  | | Places API         | | Serper.dev Search  |
| Isolated per sub | | Places Registry  | | Distance Matrix    | | BeautifulSoup4     |
| user identifier  | | (Audit trail)    | | (Walking filter)   | | & LXML Parser      |
+------------------+ +------------------+ +--------------------+ +--------------------+
```

---

## 2. Repository Structure

```
project/
├── .env.example                       # Environment variable keys template (no values)
├── .gitignore                         # Version control exclusions (ignores infra/)
├── ARCHITECTURE.md                    # Architecture documentation & diagrams
├── README.md                          # Principles, architecture, and deployment guide
├── requirements.txt                   # Production and testing Python dependencies
├── app.py                             # Flask HTTP server entrypoint for EC2
├── agent/
│   ├── __init__.py                    # Agent package initializer
│   ├── config.py                      # Strict environment variable reader & validator
│   ├── model.py                       # VertexGeminiModel provider (Google Vertex AI)
│   ├── orchestrator.py                # Agent construction, prompt, and session binding
│   ├── hooks/
│   │   ├── __init__.py                # Hooks package initializer
│   │   └── search_limits.py           # SearchLimitsHook: batch, deduplication, and safety caps
│   ├── steering/
│   │   ├── __init__.py                # Steering package initializer
│   │   └── grounding.py               # Grounding handler verifying factual consistency
│   └── tools/
│       ├── __init__.py                # Tools package initializer
│       ├── budget.py                  # Deterministic split_budget with residual absorption
│       ├── menu.py                    # Serper fallback and HTML menu scraping
│       ├── places.py                  # Google Places search & walking duration filtering
│       └── registry.py                # DynamoDB audit logging for queries and places
├── frontend/
│   └── index.html                     # Web interface (simplified email/password auth + chat)
└── tests/
    ├── __init__.py                    # Test package initializer
    ├── test_budget.py                 # Deterministic budget calculation unit tests
    ├── test_hooks.py                  # SearchLimitsHook lifecycle & safety limits tests
    ├── test_model.py                  # VertexGeminiModel tests & forbidden SDK checks
    ├── test_server.py                 # Flask routes and Cognito JWT verification tests
    └── test_tools.py                  # Tool parsing, error handling, and verification tests
```

*(Note: The `infra/` directory containing provisioning and host setup scripts is ignored from version control via `.gitignore`).*

---

## 3. Component Details & Design Decisions

### 3.1 Single LLM Provider — Google Vertex AI with Gemini
The agent relies exclusively on Google Vertex AI with Gemini as its reasoning engine. The custom provider `VertexGeminiModel` (`agent/model.py`) wraps the official `google-genai` SDK using `vertexai=True` and `GOOGLE_CLOUD_API_KEY`.
- **Zero Fallbacks:** There is no fallback logic. If Vertex AI is unreachable or encounters an error, the exception propagates directly.

### 3.2 Application Server on Amazon EC2
The compute layer runs on Amazon EC2 using a production Gunicorn WSGI server in front of Flask:
- `GET /`: Delivers `frontend/index.html` with server-injected Cognito identifiers.
- `POST /chat`: Decorator `@require_auth` verifies the incoming Cognito JWT token against Cognito's JSON Web Key Set (JWKS), extracts the user's `sub` claim, and instantiates an isolated `Agent`.

### 3.3 Session Isolation via S3SessionManager
To ensure evaluations remain completely isolated between different users, `app.py` constructs a fresh `Agent` instance on every incoming request. Persistent conversation history is managed by `S3SessionManager`, indexed strictly by the authenticated user's Cognito `sub` identifier.

### 3.4 SearchLimitsHook Deterministic Control
To prevent infinite search loops or excessive external API calls, `SearchLimitsHook` enforces:
- **Batch Size:** 15 candidates per batch.
- **Radius Progression:** Starts at 5.0 miles, widening by 2.0 miles if fewer than 20% of checked places have a published menu.
- **Cumulative Deduplication:** Accumulates `seen_place_ids` across batches and passes them via `exclude_place_ids`.
- **Target Goal:** Reaches completion once 10 to 20 places with confirmed menus are found.
- **Safety Cap:** Automatically terminates after 6 batches (maximum 90 places checked).

### 3.5 Grounding Verification via Steering
`LLMSteeringHandler` audits the candidate proposal against the tool call history. If any restaurant name, dish, or price is not present in the tool outputs from `read_web_menu`, the proposal is rejected.

---

## 4. Query Lifecycle & Filter Funnel

```
User Prompt (Budget, Location, Nutrition)
    │
    ▼
1. split_budget (Deterministic mathematical allocation across meals)
    │
    ▼
2. find_nearby_places (Google Places Nearby Search filtered by 10-min walking travel time)
    │
    ▼
3. Deduplication & Batch Control (SearchLimitsHook enforces unique candidates)
    │
    ▼
4. read_web_menu (Website verification -> Serper fallback -> BeautifulSoup4 HTML scraping)
    │
    ▼
5. Coverage Evaluation (If < 20% menu coverage, widen search radius by 2 miles)
    │
    ▼
6. Nutritional Reasoning (Vertex AI Gemini classifies dishes based on extracted descriptions)
    │
    ▼
7. Steering Verification (LLMSteeringHandler validates every dish and price against tool ledger)
    │
    ▼
8. log_result (Audit records saved to DynamoDB)
    │
    ▼
Final Transparent Proposal Delivered to User
```
