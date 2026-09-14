# BudgetFoodAgent

BudgetFoodAgent is an autonomous meal-planning agent powered by Google Vertex AI Gemini, Strands Agents, and hosted on Amazon EC2 with AWS persistence. It solves a real-world daily problem: deciding what to eat under strict budgetary and nutritional constraints using exclusively live, verified data.

---

## 1. Core Principles & Architecture

The architecture enforces strict invariants:

1. **Zero Hardcoded Data:** The system contains no predefined restaurants, menus, dishes, prices, addresses, or coordinates. Every recommendation originates from real-time live queries executed during the session.
2. **Zero Credential Fallbacks:** No credentials or tokens exist in code or version control. Environment variables are loaded strictly via `os.environ["NAME"]` without default fallbacks.
3. **Zero Fabricated Responses:** If an external source is unavailable or returns no results, the agent widens its geographic search or reports the limitation honestly.
4. **Zero LLM Provider Fallbacks:** The agent uses Google Vertex AI with Gemini as its sole LLM backend. If the Vertex AI call fails, the exception propagates directly; the system never falls back to any secondary provider.
5. **Deterministic Search Control:** Search radius expansion, batch sizing (15 places per batch), and safety caps (maximum 6 batches / 90 venues) are enforced by Python hooks, not left to probabilistic LLM decisions.
6. **Session Isolation:** A new agent instance is constructed per request, backed by Amazon S3 session storage tied to the authenticated user's Cognito identity (`sub` claim).
7. **Audit Registry:** Amazon DynamoDB maintains an immutable audit trail of queries and venue evaluations, strictly separated from answering logic.

---

## 2. System Architecture Diagram

```
+---------------------------------------------------------------------------------------+
|                                    Client Layer                                       |
|                                                                                       |
|   +--------------------------+               +------------------------------------+   |
|   | Amazon Cognito           | <===========> | Browser Frontend                   |   |
|   | User Pool & Client       |  (JWT Auth)   | (HTML5 / Vanilla JS / Cognito SDK) |   |
|   +--------------------------+               +------------------+-----------------+   |
+-----------------------------------------------------------------|---------------------+
                                                                  |
                                       HTTPS / HTTP (Port 8080)   | Authorization: Bearer <JWT>
                                                                  v
+---------------------------------------------------------------------------------------+
|                                 Amazon EC2 Instance                                   |
|                                                                                       |
|   +-------------------------------------------------------------------------------+   |
|   | Gunicorn WSGI Server (2 Workers, Timeout: 300s)                               |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   | Flask Application (`app.py`)                                                  |   |
|   |   - GET /: Serves index.html with server-injected Cognito identifiers         |   |
|   |   - POST /chat: Validates Cognito JWT via JWKS, extracts 'sub' session ID     |   |
|   +---------------------------------------+---------------------------------------+   |
|                                           |                                           |
|                                           v                                           |
|   +-------------------------------------------------------------------------------+   |
|   | Strands Agent (Built Per Invocation)                                          |   |
|   | Model: VertexGeminiModel (Google Vertex AI Gemini via google-genai)           |   |
|   |                                                                               |   |
|   |   +-----------------------------------+   +-------------------------------+   |   |
|   |   | SearchLimitsHook                  |   | LLMSteeringHandler            |   |   |
|   |   | Enforces 15/batch, deduplication, |   | Verifies all proposed dishes  |   |   |
|   |   | radius growth, 6-batch safety cap |   | are grounded in tool outputs  |   |   |
|   |   +-----------------------------------+   +-------------------------------+   |   |
|   +----+--------------------+--------------------+-----------------------+----+   |
|        |                    |                    |                       |        |
+--------|--------------------|--------------------|-----------------------|--------+
         |                    |                    |                       |
         v                    v                    v                       v
+------------------+ +------------------+ +--------------------+ +--------------------+
| S3 Storage       | | DynamoDB Tables  | | Google Maps APIs   | | Web Menu Scraping  |
|                  | |                  | |                    | |                    |
| Conversation     | | Recommendations  | | Places API         | | Serper.dev Search  |
| Session State    | | Places Registry  | | Distance Matrix    | | BeautifulSoup4     |
| (S3SessionMgr)   | | (Audit trail)    | | (Walking filter)   | | & LXML Parser      |
+------------------+ +------------------+ +--------------------+ +--------------------+
```

---

## 3. AWS & Cloud Deployment Instructions

Follow these steps sequentially to deploy BudgetFoodAgent.

### Prerequisites
- AWS CLI configured with administrator privileges (`aws configure`).
- Active Google Cloud Platform project with billing enabled.
- Active Serper.dev API key.
- Python 3.14 (or 3.13) available.

---

### Step 1: Configure Google Cloud & Vertex AI
1. In the Google Cloud Console, enable **Vertex AI API**, **Places API**, and **Distance Matrix API**.
2. Go to **APIs & Services** -> **Credentials** and create an API Key. Restrict it to Vertex AI and save it as `GOOGLE_CLOUD_API_KEY`.
3. Select your desired Gemini model ID from Vertex AI Model Garden (e.g., `gemini-2.0-flash` or `gemini-1.5-flash`) and save it as `GEMINI_MODEL_ID`.
4. Create separate or combined API keys for Maps:
   - `GOOGLE_PLACES_API_KEY`: Restricted to Places API.
   - `GOOGLE_MAPS_API_KEY`: Restricted to Distance Matrix API.
5. Obtain your Serper.dev key for menu search fallback: `SERPER_API_KEY`.

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
Create the audit tables in on-demand (`PAY_PER_REQUEST`) mode:
```bash
aws dynamodb create-table \
  --table-name budgetfoodagent-recommendations \
  --attribute-definitions AttributeName=recommendation_id,AttributeType=S \
  --key-schema AttributeName=recommendation_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_REGION"

aws dynamodb create-table \
  --table-name budgetfoodagent-places-registry \
  --attribute-definitions AttributeName=place_id,AttributeType=S \
  --key-schema AttributeName=place_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_REGION"
```

---

### Step 4: Create Cognito User Pool & Evaluator Accounts
1. Create the User Pool:
```bash
aws cognito-idp create-user-pool \
  --pool-name budgetfoodagent-judges \
  --region "$AWS_REGION"
```
*(Save the returned `Id` as `COGNITO_USER_POOL_ID`)*

2. Create the App Client:
```bash
aws cognito-idp create-user-pool-client \
  --user-pool-id "$COGNITO_USER_POOL_ID" \
  --client-name budgetfoodagent-frontend \
  --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
  --no-generate-secret \
  --region "$AWS_REGION"
```
*(Save the returned `ClientId` as `COGNITO_APP_CLIENT_ID`)*

3. Create the 4 evaluator test accounts using AWS CLI:
```bash
for i in 1 2 3 4; do
  EMAIL="judge${i}@budgetfood.internal"
  PASS="StrongPassword${i}!"
  aws cognito-idp admin-create-user \
    --user-pool-id "$COGNITO_USER_POOL_ID" \
    --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --message-action SUPPRESS \
    --region "$AWS_REGION"
  aws cognito-idp admin-set-user-password \
    --user-pool-id "$COGNITO_USER_POOL_ID" \
    --username "$EMAIL" \
    --password "$PASS" \
    --permanent \
    --region "$AWS_REGION"
  echo "Evaluator account created: $EMAIL"
done
```

---

### Step 5: Launch & Configure EC2 Instance
1. Launch an EC2 instance (Amazon Linux 2023 or Ubuntu 24.04 LTS, instance type `t3.small` or `t3.medium`).
2. Attach an IAM Instance Profile granting:
   - `dynamodb:PutItem`, `dynamodb:UpdateItem` on the two DynamoDB tables.
   - `s3:GetObject`, `s3:PutObject` on the `SESSIONS_S3_BUCKET`.
3. In the Security Group, add an Inbound Rule for TCP port `8080` (or `80`/`443`).
4. SSH into the instance:
```bash
ssh -i your-key.pem ec2-user@<instance-public-ip>
```
5. Clone the repository into `/home/ec2-user/budgetfoodagent`:
```bash
git clone <repo-url> /home/ec2-user/budgetfoodagent
cd /home/ec2-user/budgetfoodagent
```
6. Create the private environment file `/home/ec2-user/budgetfoodagent/.env` (`chmod 600`):
```bash
cat << 'EOF' > /home/ec2-user/budgetfoodagent/.env
AWS_REGION=us-east-1
GOOGLE_CLOUD_API_KEY=<your-vertex-ai-key>
GEMINI_MODEL_ID=gemini-2.0-flash
GOOGLE_PLACES_API_KEY=<your-google-places-key>
GOOGLE_MAPS_API_KEY=<your-google-maps-key>
SERPER_API_KEY=<your-serper-key>
DYNAMODB_TABLE_RECOMMENDATIONS=budgetfoodagent-recommendations
DYNAMODB_TABLE_PLACES_REGISTRY=budgetfoodagent-places-registry
COGNITO_USER_POOL_ID=<your-user-pool-id>
COGNITO_APP_CLIENT_ID=<your-app-client-id>
SESSIONS_S3_BUCKET=<your-sessions-bucket>
EOF
chmod 600 /home/ec2-user/budgetfoodagent/.env
```
7. Set up the Python virtual environment and systemd service:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

sudo tee /etc/systemd/system/budgetfoodagent.service > /dev/null << 'EOF'
[Unit]
Description=BudgetFoodAgent
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/budgetfoodagent
EnvironmentFile=/home/ec2-user/budgetfoodagent/.env
ExecStart=/home/ec2-user/budgetfoodagent/.venv/bin/gunicorn --bind 0.0.0.0:8080 --workers 2 --timeout 300 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable budgetfoodagent
sudo systemctl start budgetfoodagent
```

---

### Step 6: Live Verification
Open your browser and navigate to:
```
http://<instance-public-ip>:8080/
```
Log in using any of the provisioned evaluator credentials (`judge1@budgetfood.internal`) and enter a meal-planning query. The proposal will be generated using live Google Places data and Google Vertex AI Gemini reasoning.
