import base64
import json
import logging
import os
import boto3

logger = logging.getLogger(__name__)


def handler(event, context):
    http_ctx = event.get("requestContext", {}).get("http", {})
    route = http_ctx.get("path") or event.get("rawPath", "")
    method = http_ctx.get("method", "")

    if route in ("/", "/index.html") and method == "GET":
        candidate_paths = [
            os.path.join(
                os.path.dirname(__file__), "..", "..", "frontend", "index.html"
            ),
            os.path.join(os.path.dirname(__file__), "frontend", "index.html"),
            "frontend/index.html",
        ]
        html_content = None
        for path in candidate_paths:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                break

        if html_content is None:
            logger.error("Frontend index.html asset could not be located.")
            return {"statusCode": 500, "body": "Frontend asset not found"}

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": html_content,
        }

    if route == "/chat" and method == "POST":
        raw_body = event.get("body", "")
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        try:
            body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Failed to parse request JSON body: %s", exc)
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Invalid JSON payload: {exc}"}),
            }

        prompt = body.get("prompt")
        if not prompt:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'prompt'"}),
            }

        authorizer = event.get("requestContext", {}).get("authorizer", {})
        jwt_info = authorizer.get("jwt", {})
        claims = jwt_info.get("claims", {})
        session_id = claims.get("sub") or authorizer.get("claims", {}).get("sub")

        if not session_id:
            logger.error("User identifier 'sub' claim missing from JWT authorizer context")
            return {
                "statusCode": 401,
                "body": json.dumps({"error": "Unauthorized: missing user identifier"}),
            }

        agentcore_client = boto3.client(
            "bedrock-agentcore", region_name=os.environ["AWS_REGION"]
        )
        runtime_arn = os.environ["AGENTCORE_RUNTIME_ARN"]

        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            payload=json.dumps({"prompt": prompt, "session_id": session_id}).encode(
                "utf-8"
            ),
        )
        content = response["response"].read().decode("utf-8")

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": content,
        }

    return {"statusCode": 404, "body": "Not found"}
