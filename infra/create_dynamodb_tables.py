import logging
import os
import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_dynamodb_tables() -> None:
    region = os.environ["AWS_REGION"]
    table_recs_name = os.environ["DYNAMODB_TABLE_RECOMMENDATIONS"]
    table_places_name = os.environ["DYNAMODB_TABLE_PLACES_REGISTRY"]

    dynamodb = boto3.client("dynamodb", region_name=region)

    table_definitions = [
        {
            "TableName": table_recs_name,
            "KeySchema": [{"AttributeName": "recommendation_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "recommendation_id", "AttributeType": "S"}
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": table_places_name,
            "KeySchema": [{"AttributeName": "place_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "place_id", "AttributeType": "S"}
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]

    for table_spec in table_definitions:
        table_name = table_spec["TableName"]
        try:
            logger.info("Creating DynamoDB table: %s...", table_name)
            dynamodb.create_table(**table_spec)
            waiter = dynamodb.get_waiter("table_exists")
            waiter.wait(TableName=table_name)
            logger.info("Table %s created and active.", table_name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceInUseException":
                logger.info("Table %s already exists.", table_name)
            else:
                logger.error("Failed to create table %s: %s", table_name, exc)
                raise


if __name__ == "__main__":
    create_dynamodb_tables()
