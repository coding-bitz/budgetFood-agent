from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import uuid
import boto3
from strands import tool

logger = logging.getLogger(__name__)


def _to_decimal(val):
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, dict):
        return {k: _to_decimal(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_to_decimal(x) for x in val]
    return val


@tool
def log_result(
    location: dict,
    total_budget: float,
    places_checked: list[dict],
    final_proposal: list[dict],
) -> str:
    """
    Logs to DynamoDB, for auditing, the complete result of a query:
    - In the DYNAMODB_TABLE_PLACES_REGISTRY table: one item per place
      checked (whether or not it ended up in the final proposal), with its
      place_id, name, address, whether it had a website, whether it had a
      menu, and when it was checked.
    - In the DYNAMODB_TABLE_RECOMMENDATIONS table: one item with the
      complete final proposal delivered to the user.

    Returns the generated recommendation_id (UUID).
    This is an audit log, not a data source to answer from — this table is
    never read to fabricate a response without a live search having happened
    in the current run.
    """
    region = os.environ["AWS_REGION"]
    table_recs_name = os.environ["DYNAMODB_TABLE_RECOMMENDATIONS"]
    table_places_name = os.environ["DYNAMODB_TABLE_PLACES_REGISTRY"]

    recommendation_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    dynamodb = boto3.resource("dynamodb", region_name=region)
    recs_table = dynamodb.Table(table_recs_name)
    places_table = dynamodb.Table(table_places_name)

    rec_item = {
        "recommendation_id": recommendation_id,
        "created_at": now_iso,
        "location": _to_decimal(location),
        "total_budget": Decimal(str(total_budget)),
        "proposal": json.dumps(final_proposal),
    }
    recs_table.put_item(Item=rec_item)

    with places_table.batch_writer() as batch:
        for place in places_checked:
            pid = place.get("place_id")
            if not pid:
                continue

            place_item = {
                "place_id": str(pid),
                "name": str(place.get("name") or ""),
                "address": str(place.get("address") or ""),
                "has_website": bool(place.get("has_website")),
                "has_menu": bool(place.get("has_menu")),
                "last_checked_at": now_iso,
            }
            if place.get("website"):
                place_item["website"] = str(place["website"])
            if place.get("menu_url"):
                place_item["menu_url"] = str(place["menu_url"])

            batch.put_item(Item=place_item)

    return recommendation_id
