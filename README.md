# BudgetFoodAgent

BudgetFoodAgent is an autonomous meal-planning agent powered by AWS Bedrock, Claude 3.5 Sonnet, and Strands Agents. It solves a real-world daily problem: deciding what to eat under strict budgetary and nutritional constraints using solely live, verified data.

---

## 1. Core Principles & Architecture

The architecture enforces strict invariants:

1. **Zero Hardcoded Data:** The system contains no predefined restaurants, menus, dishes, prices, addresses, or coordinates. Every recommendation originates from real-time live queries executed during the session.
2. **Zero Credential Fallbacks:** No credentials or tokens exist in code or version control. Environment variables are loaded strictly via `os.environ["NAME"]` without default fallbacks.
3. **Zero Fabricated Responses:** If an external source is unavailable or returns no results, the agent widens its geographic search or reports the limitation honestly.
4. **Deterministic Search Control:** Search radius expansion, batch sizing (15 places per batch), and safety caps (maximum 6 batches / 90 venues) are enforced by Python hooks, not left to probabilistic LLM decisions.
5. **Session Isolation:** A new agent instance is constructed per request, backed by Amazon S3 session storage tied to the authenticated user's Cognito identity (`sub` claim).
6. **Audit Registry:** Amazon DynamoDB maintains an immutable audit trail of queries and venue evaluations, strictly separated from answering logic.

---

## 2. System Architecture Diagram

```
+-----------------------------------------------------------------------------------+
|                                  Client Layer                                     |
|                                                                                   |
|    +------------------------+             +----------------------------------+    |
|    | Amazon Cognito         | <=========> | Browser Frontend                 |    |
|    | User Pool & Client     |  (JWT Auth) | (HTML5 / Vanilla JS / CDN SDK)   |    |
|    +------------------------+             +-----------------+----------------+    |
+-------------------------------------------------------------|---------------------+
                                                              |
                                           HTTPS POST /chat   | Authorization: Bearer <JWT>
                                           HTTPS GET /        |
                                                              v
+-----------------------------------------------------------------------------------+
|                                 Serverless Proxy                                  |
|                                                                                   |
|    +-------------------------------------------------------------------------+    |
|    | Amazon API Gateway (HTTP API v2)                                        |    |
|    | JWT Authorizer (validates issuer & audience, passes 'sub' claim)        |    |
|    +------------------------------------+------------------------------------+    |
|                                         |                                         |
|                                         v                                         |
|    +-------------------------------------------------------------------------+    |
|    | AWS Lambda Proxy Handler                                                |    |
|    | Routes GET / to index.html; extracts 'sub' as session_id for POST /chat |    |
|    +------------------------------------+------------------------------------+    |
+-----------------------------------------|-----------------------------------------+
                                          |
                                          | boto3 invoke_agent_runtime
                                          v
+-----------------------------------------------------------------------------------+
|                             Bedrock AgentCore Runtime                             |
|                                                                                   |
|   +---------------------------------------------------------------------------+   |
|   | app.py (BedrockAgentCoreApp Entrypoint)                                   |   |
|   +-------------------------------------+-------------------------------------+   |
|                                         |                                         |
|                                         v                                         |
|   +---------------------------------------------------------------------------+   |
|   | Strands Agent (Built Per Invocation)                                      |   |
|   | Model: Anthropic Claude 3.5 Sonnet on Amazon Bedrock                      |   |
|   |                                                                           |   |
|   |  +---------------------------------+  +--------------------------------+  |   |
|   |  | SearchLimitsHook                |  | LLMSteeringHandler             |  |   |
|   |  | Enforces 15/batch, deduplication|  | Verifies all proposed dishes   |  |   |
|   |  | radius growth, 6-batch safety cap|  | are grounded in tool outputs  |  |   |
|   |  +---------------------------------+  +--------------------------------+  |   |
|   +----+-------------------+--------------------+------------------------+----+   |
|        |                   |                    |                        |        |
+--------|-------------------|--------------------|------------------------|--------+
         |                   |                    |                        |
         v                   v                    v                        v
+-----------------+ +-----------------+ +--------------------+ +--------------------+
| S3 Storage      | | DynamoDB Audit  | | Google Maps APIs   | | Web Menu Sources   |
|                 | |                 | |                    | |                    |
| Conversation    | | Recommendations | | Places API         | | Serper.dev Search  |
| Session State   | | Places Registry | | Distance Matrix    | | BeautifulSoup4     |
| (S3SessionMgr)  | | (On-Demand)     | | (Walking Duration) | | HTML & LXML Parser |
+-----------------+ +-----------------+ +--------------------+ +--------------------+
```

---

## 3. AWS Deployment Instructions

Follow these steps sequentially to deploy BudgetFoodAgent.

### Prerequisites
- AWS CLI configured with administrator privileges (`aws configure`).
- Active Google Cloud Platform API key with **Places API** and **Distance Matrix API** enabled.
- Active **Serper.dev** API key.
- Python 3.14 (or 3.13) and Node 24 available locally.

---

### Step 1: Enable Claude 3.5 Sonnet on Amazon Bedrock
1. Open the **Amazon Bedrock Console** in your target region (e.g., `us-east-1`).
2. Go to **Model access** -> **Modify model access**.
3. Select **Anthropic Claude 3.5 Sonnet** and submit the access request.
4. Verify that access displays **Access granted**. Note your Model ID.

---

### Step 2: Create S3 Bucket for Sessions
Create a private S3 bucket dedicated to storing persistent conversation history:
```bash
export AWS_REGION="us-east-1"
export SESSIONS_S3_BUCKET="budgetfoodagent-sessions-$(aws sts get-caller-identity --query Account --output text)-${AWS_REGION}"

aws s3api create-bucket \
  --bucket "$SESSIONS_S3_BUCKET" \
  --region "$AWS_REGION"
```

---

### Step 3: Create DynamoDB Audit Tables
Run the automated infrastructure provisioning script:
```bash
export DYNAMODB_TABLE_RECOMMENDATIONS="budgetfoodagent-recommendations"
export DYNAMODB_TABLE_PLACES_REGISTRY="budgetfoodagent-places-registry"

python infra/create_dynamodb_tables.py
```
This provisions `budgetfoodagent-recommendations` and `budgetfoodagent-places-registry` in on-demand (`PAY_PER_REQUEST`) mode.

---

### Step 4: Configure Local Environment
Export your API keys and configuration variables:
```bash
export BEDROCK_MODEL_ID="anthropic.claude-3-5-sonnet-20241022-v2:0"
export GOOGLE_PLACES_API_KEY="your-google-places-key"
export GOOGLE_MAPS_API_KEY="your-google-maps-key"
export SERPER_API_KEY="your-serper-key"
```

---

### Step 5: Test Agent Locally
Verify agent behavior and hooks before cloud packaging:
```bash
python app.py
```
In a separate terminal, test the invocation endpoint with a location query:
```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "I have 40 dollars for lunch and dinner near 350 5th Ave New York, high-protein midday and light at night", "session_id": "test-session-1"}'
```

---

### Step 6: Deploy Bedrock AgentCore Runtime
Deploy the containerized agent runtime:
```bash
agentcore configure --entrypoint app.py --region "$AWS_REGION"
agentcore launch
```
Note the ARN returned by the command and assign it:
```bash
export AGENTCORE_RUNTIME_ARN="<arn-returned-by-agentcore-launch>"
```

---

### Step 7: Provision Amazon Cognito
Create the User Pool and App Client:
```bash
aws cognito-idp create-user-pool \
  --pool-name budgetfoodagent-judges \
  --region "$AWS_REGION"

export COGNITO_USER_POOL_ID="<returned-user-pool-id>"

aws cognito-idp create-user-pool-client \
  --user-pool-id "$COGNITO_USER_POOL_ID" \
  --client-name budgetfoodagent-frontend \
  --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
  --no-generate-secret \
  --region "$AWS_REGION"

export COGNITO_APP_CLIENT_ID="<returned-client-id>"
```

Provision evaluator accounts:
```bash
export JUDGE1_EMAIL="evaluator1@budgetfood.local"
export JUDGE1_PASSWORD="StrongPassword123!"
export JUDGE2_EMAIL="evaluator2@budgetfood.local"
export JUDGE2_PASSWORD="StrongPassword123!"
export JUDGE3_EMAIL="evaluator3@budgetfood.local"
export JUDGE3_PASSWORD="StrongPassword123!"
export JUDGE4_EMAIL="evaluator4@budgetfood.local"
export JUDGE4_PASSWORD="StrongPassword123!"

python infra/create_cognito_users.py
```

---

### Step 8: Deploy API Gateway & Lambda Proxy
1. Package and deploy `infra/lambda_proxy/handler.py` as an AWS Lambda function with the execution role having `bedrock-agentcore:InvokeAgentRuntime` permissions.
2. Configure an Amazon API Gateway HTTP API v2:
   - Create a **JWT Authorizer** using Issuer URL `https://cognito-idp.${AWS_REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}` and Audience `${COGNITO_APP_CLIENT_ID}`.
   - Route `GET /` pointing to the Lambda (public, no authorizer).
   - Route `POST /chat` pointing to the Lambda (protected with the Cognito JWT authorizer).
3. Deploy the API to a stage.

---

### Step 9: Final Public Verification
Access the deployed API Gateway endpoint:
```
Public URL: https://<api-id>.execute-api.<region>.amazonaws.com/
```
Log in using any of the provisioned evaluator credentials to start planning meals with live data.
