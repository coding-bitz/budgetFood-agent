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
                                       HTTPS (GET / and POST /chat)|
                                                                  v
+---------------------------------------------------------------------------------------+
|                                  API Gateway & Proxy                                  |
|                                                                                       |
|   +-------------------------------------------------------------------------------+   |
|   | Amazon API Gateway (HTTP API v2)                                              |   |
|   |   - Route GET /  --> Public                                                   |   |
|   |   - Route POST /chat --> JWT Authorizer (validates Cognito ID token)          |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   | AWS Lambda Proxy (`infra/lambda_proxy/handler.py`)                            |   |
|   |   - GET /: Returns frontend/index.html                                        |   |
|   |   - POST /chat: Extracts 'sub' claim as session_id and calls AgentCore        |   |
|   +---------------------------------------+---------------------------------------+   |
+-------------------------------------------|-------------------------------------------+
                                            |
                                            | invoke_agent_runtime
                                            v
+---------------------------------------------------------------------------------------+
|                               Bedrock AgentCore Container                             |
|                                                                                       |
|   +-------------------------------------------------------------------------------+   |
|   | Entrypoint (`app.py` -> BedrockAgentCoreApp)                                  |   |
|   | Builds dedicated Agent per invocation with session_id                         |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   | Strands Agent (`agent/orchestrator.py`)                                       |   |
|   | Model: Anthropic Claude 3.5 Sonnet via BedrockModel                           |   |
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
├── .gitignore                         # Version control exclusions
├── ARCHITECTURE.md                    # Architecture documentation & diagrams
├── README.md                          # Principles, architecture, and AWS deployment
├── requirements.txt                   # Production and testing Python dependencies
├── app.py                             # Bedrock AgentCore container application entrypoint
├── agent/
│   ├── __init__.py                    # Agent package initializer
│   ├── config.py                      # Strict environment variable reader & validator
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
│   └── index.html                     # Single-page web interface (Cognito auth + chat UI)
├── infra/
│   ├── create_cognito_users.py        # Evaluator accounts provisioning script
│   ├── create_dynamodb_tables.py      # On-demand DynamoDB tables provisioning script
│   └── lambda_proxy/
│       └── handler.py                 # Lambda function serving frontend and routing requests
└── tests/
    ├── __init__.py                    # Test package initializer
    ├── test_budget.py                 # Deterministic budget calculation unit tests
    ├── test_hooks.py                  # SearchLimitsHook lifecycle & safety limits tests
    └── test_tools.py                  # Tool parsing, error handling, and verification tests
```

---

## 3. Component Details & Design Decisions

### 3.1 Session Manager & Orchestrator Isolation
To prevent conversation leakage between different users or evaluators, the orchestrator constructs a new `Agent` instance for every incoming request inside `app.py`. Session history is persisted in Amazon S3 via `S3SessionManager`, indexed strictly by the authenticated user's Cognito `sub` identifier.

### 3.2 SearchLimitsHook Guardrails
Autonomous search without code-level boundaries risks infinite loops or unbounded API usage. `SearchLimitsHook` enforces:
- **Batch Size:** Fixed at 15 venues per batch.
- **Radius Progression:** Starts at 5.0 miles, widening by 2.0 miles if fewer than 20% of checked places have a published menu.
- **Cumulative Deduplication:** Passes `exclude_place_ids` to `find_nearby_places` to guarantee venues evaluated in earlier batches are never revisited.
- **Stopping Criteria:** Halts when 10 to 20 venues with confirmed menus are found.
- **Safety Cap:** Automatically terminates after 6 batches (maximum 90 places evaluated), ensuring deterministic termination even in sparse coverage areas.

### 3.3 LLM Steering Grounding Verification
`LLMSteeringHandler` reviews the agent's proposed response against the conversation's tool ledger before delivery. If a restaurant name, dish, or price cannot be traced to actual tool output from `read_web_menu`, the response is rejected, preventing hallucinations.

### 3.4 DynamoDB Audit Architecture
Two on-demand (`PAY_PER_REQUEST`) DynamoDB tables store runtime audits:
1. `budgetfoodagent-recommendations`: Stores full proposals, timestamps, locations, and assigned budgets.
2. `budgetfoodagent-places-registry`: Stores every evaluated venue, website discovery status, menu extraction results, and check timestamps.

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
6. Nutritional Reasoning (LLM classifies dishes based on extracted descriptions)
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
