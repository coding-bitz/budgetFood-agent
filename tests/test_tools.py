import json
from unittest.mock import MagicMock, patch
import pytest
from agent.tools.menu import read_web_menu
from agent.tools.places import find_nearby_places
from agent.tools.registry import log_result


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("GOOGLE_CLOUD_API_KEY", "test-google-cloud-key")
    monkeypatch.setenv("GEMINI_MODEL_ID", "gemini-2.0-flash")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-places-key")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-maps-key")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    monkeypatch.setenv("DYNAMODB_TABLE_RECOMMENDATIONS", "test-recs-table")
    monkeypatch.setenv("DYNAMODB_TABLE_PLACES_REGISTRY", "test-places-table")
    monkeypatch.setenv("SESSIONS_S3_BUCKET", "test-sessions-bucket")


def test_places_tool_filters_exclude_place_ids():
    nearby_data = {
        "status": "OK",
        "results": [
            {
                "place_id": "pid_1",
                "name": "Deli 1",
                "vicinity": "123 Main St",
                "geometry": {"location": {"lat": 40.71, "lng": -74.00}},
            },
            {
                "place_id": "pid_2",
                "name": "Deli 2",
                "vicinity": "124 Main St",
                "geometry": {"location": {"lat": 40.72, "lng": -74.01}},
            },
        ],
    }

    dm_data = {
        "status": "OK",
        "rows": [{"elements": [{"status": "OK", "duration": {"value": 300}}]}],
    }

    details_data = {
        "status": "OK",
        "result": {
            "name": "Deli 2",
            "formatted_address": "124 Main St, New York, NY",
            "geometry": {"location": {"lat": 40.72, "lng": -74.01}},
            "website": "https://deli2.com",
            "price_level": 2,
        },
    }

    def mock_get(url, *args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        if "nearbysearch" in url:
            mock_resp.json.return_value = nearby_data
        elif "distancematrix" in url:
            mock_resp.json.return_value = dm_data
        elif "details" in url:
            mock_resp.json.return_value = details_data
        return mock_resp

    with patch("requests.get", side_effect=mock_get):
        places = find_nearby_places(
            latitude=40.71,
            longitude=-74.00,
            exclude_place_ids=["pid_1"],  # Exclude pid_1
        )

        assert len(places) == 1
        assert places[0]["place_id"] == "pid_2"
        assert places[0]["website"] == "https://deli2.com"
        assert places[0]["price_range"] == "$$"


def test_places_tool_propagates_api_error():
    error_data = {"status": "REQUEST_DENIED", "error_message": "Invalid API key"}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = error_data

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="Google Places API error"):
            find_nearby_places(latitude=40.71, longitude=-74.00)


def test_menu_tool_with_valid_menu():
    html_content = """
    <html>
      <body>
        <div class="menu-item">
          <h4>Grilled Chicken Bowl</h4>
          <span class="price">$14.50</span>
          <p class="description">Brown rice, grilled chicken breast, seasonal vegetables</p>
        </div>
      </body>
    </html>
    """

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = html_content

    with patch("requests.get", return_value=mock_resp):
        result = read_web_menu(
            place_id="pid_test",
            place_name="Healthy Cafe",
            address="100 Broadway",
            website="https://healthycafe.com",
        )

        assert result["has_website"] is True
        assert result["has_menu"] is True
        assert len(result["dishes"]) == 1
        assert result["dishes"][0]["name"] == "Grilled Chicken Bowl"
        assert result["dishes"][0]["price"] == 14.50
        assert "Brown rice" in result["dishes"][0]["description"]


def test_menu_tool_serper_fallback():
    serper_data = {
        "organic": [
            {"title": "Aggregator", "link": "https://www.yelp.com/biz/bistro"},
            {"title": "Official Site", "link": "https://bistronyc.com"},
        ]
    }

    mock_post_resp = MagicMock()
    mock_post_resp.raise_for_status = MagicMock()
    mock_post_resp.json.return_value = serper_data

    html_content = """
    <html><body><div class="item"><h3>Salmon Salad</h3><span>$18.00</span></div></body></html>
    """
    mock_get_resp = MagicMock()
    mock_get_resp.raise_for_status = MagicMock()
    mock_get_resp.text = html_content

    with patch("requests.post", return_value=mock_post_resp), patch(
        "requests.get", return_value=mock_get_resp
    ):
        result = read_web_menu(
            place_id="pid_bistro",
            place_name="Bistro NYC",
            address="200 Park Ave",
            website=None,  # Forces Serper lookup
        )

        assert result["has_website"] is True
        assert result["has_menu"] is True
        assert result["dishes"][0]["name"] == "Salmon Salad"
        assert result["dishes"][0]["price"] == 18.00


def test_registry_tool_dynamodb_audit():
    mock_recs_table = MagicMock()
    mock_places_table = MagicMock()
    mock_batch = MagicMock()
    mock_places_table.batch_writer.return_value.__enter__.return_value = mock_batch

    def mock_table(name):
        if name == "test-recs-table":
            return mock_recs_table
        return mock_places_table

    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.side_effect = mock_table

    with patch("boto3.resource", return_value=mock_dynamodb):
        rec_id = log_result(
            location={"lat": 40.71, "lng": -74.00},
            total_budget=40.0,
            places_checked=[
                {
                    "place_id": "pid_10",
                    "name": "Cafe 10",
                    "address": "10 Pine St",
                    "has_website": True,
                    "has_menu": True,
                }
            ],
            final_proposal=[
                {"meal": "lunch", "place": "Cafe 10", "dish": "Bowl", "price": 15.0}
            ],
        )

        assert rec_id is not None
        assert len(rec_id) > 10
        mock_recs_table.put_item.assert_called_once()
        mock_batch.put_item.assert_called_once()
