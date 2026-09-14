# BudgetFoodAgent

BudgetFoodAgent is an autonomous meal-planning agent powered by Strands Agents, Google Vertex AI Gemini, hosted on Amazon EC2 with AWS persistence. It solves a real-world daily problem: deciding what to eat under strict budgetary and nutritional constraints using exclusively live, verified data.

---

## 1. Core Functions

BudgetFoodAgent coordinates autonomous meal planning using modular tools, hooks, and verification steering:

### Functions (Agent Tools)
- **`split_budget`**: Deterministically divides the user's budget across meals (e.g. lunch and dinner), absorbing rounding residuals to ensure exact sums down to the cent.
- **`find_nearby_places`**: Discovers food venues using Google Places API and filters out any venue exceeding a 10-minute walk using Google Maps travel-time calculations.
- **`read_web_menu`**: Retrieves restaurant web pages (using Serper search fallback if needed) and extracts real dishes, descriptions, and exact prices via BeautifulSoup and lxml parsing.
- **`log_result`**: Persists audit entries to DynamoDB (`recommendations` and `places-registry` tables) for transparency and verification.

### The Agent
- **Strands Autonomous Agent**: Powered by Google Vertex AI Gemini (`VertexGeminiModel`). It reasons over user preferences, plans necessary search batches, interprets live menu data, and composes realistic meal proposals without fabricating options.

### Hooks
- **`SearchLimitsHook`**: Deterministic lifecycle guardrail. Sets batch sizes to 15 venues, tracks previously evaluated places across batches, expands the search radius by 2 miles when published menu coverage is low (< 20%), and enforces a safety cap of 6 batches (90 places maximum) to prevent infinite loops.

### Steering
- **`LLMSteeringHandler` (Grounding Plugin)**: Audits the final response against the conversation's tool history ledger. If any dish, price, or restaurant cannot be verified against live tool outputs, the response is rejected to prevent hallucinations.

### Skills & Reasoning
- **Nutritional Classification**: The agent evaluates dish titles and ingredient descriptions directly to verify dietary criteria (e.g., high-protein, vegetarian, light meals) without relying on artificial estimations.

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
