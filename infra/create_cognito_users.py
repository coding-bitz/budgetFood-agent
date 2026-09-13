import logging
import os
import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_cognito_evaluator_accounts() -> None:
    region = os.environ["AWS_REGION"]
    user_pool_id = os.environ["COGNITO_USER_POOL_ID"]

    cognito = boto3.client("cognito-idp", region_name=region)

    for i in range(1, 5):
        email = os.environ[f"JUDGE{i}_EMAIL"]
        password = os.environ[f"JUDGE{i}_PASSWORD"]

        try:
            cognito.admin_create_user(
                UserPoolId=user_pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                MessageAction="SUPPRESS",
            )
            logger.info("Cognito user created: %s", email)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "UsernameExistsException":
                logger.info("Cognito user %s already exists. Updating password.", email)
            else:
                logger.error("Failed to create user %s: %s", email, exc)
                raise

        cognito.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=email,
            Password=password,
            Permanent=True,
        )
        logger.info("Password set successfully for user: %s", email)


if __name__ == "__main__":
    create_cognito_evaluator_accounts()
