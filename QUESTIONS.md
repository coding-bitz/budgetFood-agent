# Technical Decisions & Inquiries Log

This document records technical decisions, clarifications, and architecture considerations made during development, following the conservative and zero-fabricated-data principles.

---

### 1. Google Maps Platform Travel-Time API
- **Context:** `implementation.md` specifies using the current Google Maps Platform travel-time API to evaluate walking time (`max_walk_time_minutes`).
- **Decision:** Use Google Maps Distance Matrix API with `mode=walking` (or the Routes API computeRouteMatrix if configured). The implementation queries walking travel duration in seconds, rejects any place exceeding `max_walk_time_minutes * 60`, and handles API errors explicitly without silent fallbacks.

### 2. API Gateway HTTP API v2 Authorizer Claims Structure
- **Context:** API Gateway HTTP APIs with JWT authorizer pass token claims inside the event context.
- **Decision:** Claims are retrieved via `event["requestContext"]["authorizer"]["jwt"]["claims"]` conforming to API Gateway Payload Format Version 2.0. The `sub` claim is extracted to serve as the persistent `session_id`.

### 3. Bedrock AgentCore Runtime Client Method
- **Context:** The Lambda proxy invokes the deployed AgentCore runtime container.
- **Decision:** Use `boto3.client("bedrock-agentcore", region_name=...)` with `invoke_agent_runtime` as targeted in the runtime specification, handling exceptions explicitly and passing the JSON-encoded payload containing `prompt` and `session_id`.

### 4. Search Limits Batch Safety Cap
- **Context:** To ensure the search loop terminates deterministically when menu availability is sparse.
- **Decision:** A deterministic cap of 6 batches (maximum 90 unique places evaluated) is enforced by `SearchLimitsHook`. If the cap is reached before collecting 10 places with confirmed menus, the agent builds the proposal using available verified options, explicitly declaring the sparse coverage to the user.
