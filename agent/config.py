import os

REQUIRED_ENV_VARS = (
    "AWS_REGION",
    "GOOGLE_CLOUD_API_KEY",
    "GEMINI_MODEL_ID",
    "GOOGLE_PLACES_API_KEY",
    "GOOGLE_MAPS_API_KEY",
    "SERPER_API_KEY",
    "DYNAMODB_TABLE_RECOMMENDATIONS",
    "DYNAMODB_TABLE_PLACES_REGISTRY",
    "SESSIONS_S3_BUCKET",
)


def validate_environment() -> None:
    for var_name in REQUIRED_ENV_VARS:
        if var_name not in os.environ or not os.environ[var_name]:
            raise KeyError(f"Required environment variable '{var_name}' is not set.")


def get_env_var(name: str) -> str:
    value = os.environ[name]
    if not value:
        raise ValueError(f"Environment variable '{name}' cannot be empty.")
    return value
