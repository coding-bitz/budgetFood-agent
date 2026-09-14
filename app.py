from functools import wraps
import json
import logging
import os
from flask import Flask, jsonify, request
import jwt
from jwt.algorithms import RSAAlgorithm
import requests as http_requests

from agent.orchestrator import build_agent

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="frontend", static_url_path="")

# Cognito configuration — read once at startup, injected into the HTML
COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
COGNITO_APP_CLIENT_ID = os.environ["COGNITO_APP_CLIENT_ID"]
AWS_REGION = os.environ["AWS_REGION"]

_jwks_cache = None


def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        jwks_url = (
            f"https://cognito-idp.{AWS_REGION}.amazonaws.com/"
            f"{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
        )
        response = http_requests.get(jwks_url, timeout=10)
        response.raise_for_status()
        _jwks_cache = response.json()
    return _jwks_cache


def _validate_cognito_token(token: str) -> dict:
    """
    Validates a Cognito JWT token and returns its claims.
    Raises an exception if the token is invalid.
    """
    global _jwks_cache
    jwks = _get_jwks()
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")

    key = None
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            key = RSAAlgorithm.from_jwk(json.dumps(k))
            break

    # If kid not found in cache, re-fetch once to support key rotation
    if key is None:
        _jwks_cache = None
        jwks = _get_jwks()
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = RSAAlgorithm.from_jwk(json.dumps(k))
                break

    if key is None:
        raise ValueError("No matching key found in Cognito JWKS")

    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=COGNITO_APP_CLIENT_ID,
        issuer=f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}",
    )
    return claims


def require_auth(f):
    """Decorator that validates the Cognito JWT from the Authorization header."""

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header[7:]
        try:
            claims = _validate_cognito_token(token)
        except Exception as exc:
            logger.warning("Token validation failed: %s", exc)
            return jsonify({"error": "Invalid token"}), 401

        request.user_claims = claims
        return f(*args, **kwargs)

    return decorated


@app.route("/")
def index():
    """Serves the frontend with Cognito config injected server-side."""
    with open(os.path.join("frontend", "index.html"), "r", encoding="utf-8") as f:
        html = f.read()

    # Inject Cognito configuration so the user only provides credentials
    html = html.replace("{{COGNITO_USER_POOL_ID}}", COGNITO_USER_POOL_ID)
    html = html.replace("{{COGNITO_APP_CLIENT_ID}}", COGNITO_APP_CLIENT_ID)
    html = html.replace("{{AWS_REGION}}", AWS_REGION)

    return html


@app.route("/chat", methods=["POST"])
@require_auth
def chat():
    """
    Handles a chat request. The user's identity comes from the validated
    Cognito JWT — the sub claim is used as session_id.
    """
    body = request.get_json()
    if not body or not body.get("prompt"):
        return jsonify({"error": "Missing 'prompt' in request body"}), 400

    prompt = body["prompt"]
    session_id = request.user_claims["sub"]

    # A new Agent per invocation maintains clean session isolation per user
    agent = build_agent(session_id=session_id)
    response = agent(prompt)

    return jsonify({"response": str(response)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
