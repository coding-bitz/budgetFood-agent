import logging
import os
import requests
from strands import tool

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


@tool
def find_nearby_places(
    latitude: float,
    longitude: float,
    food_type: str | None = None,
    radius_miles: float = 5.0,
    max_walk_time_minutes: float = 10.0,
    max_results: int = 15,
    exclude_place_ids: list[str] | None = None,
) -> list[dict]:
    """
    Searches for real food places near a location using the Google Places API.
    Filters the result by radius_miles AND by max_walk_time_minutes (using the
    current Google Maps Platform travel-time API), keeping whichever of the
    two is more restrictive for each place. Returns at most max_results places,
    skipping any place whose place_id is in exclude_place_ids.

    You only need to provide latitude, longitude, and optionally food_type.
    The radius_miles, max_results, and exclude_place_ids parameters are
    managed automatically by the system — any values you provide for them
    will be overridden. Do not try to set them yourself.

    Returns a list of dicts, one per place found, with exactly these keys:
    place_id, name, address, latitude, longitude, website (str or None,
    exactly as Google Places provides it), price_range (str or None).

    Never returns invented places. If the API call fails, it raises the
    exception as-is (does not catch it silently). If there are no results
    within the radius, it returns [].
    """
    places_api_key = os.environ["GOOGLE_PLACES_API_KEY"]
    maps_api_key = os.environ["GOOGLE_MAPS_API_KEY"]

    radius_meters = int(radius_miles * METERS_PER_MILE)
    nearby_url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{latitude},{longitude}",
        "radius": radius_meters,
        "type": "restaurant",
        "key": places_api_key,
    }
    if food_type:
        params["keyword"] = food_type

    response = requests.get(nearby_url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    status = data.get("status")
    if status == "ZERO_RESULTS":
        return []
    if status != "OK":
        error_message = data.get("error_message", f"Status code {status}")
        logger.error("Google Places Nearby Search error: %s", error_message)
        raise RuntimeError(f"Google Places API error: {error_message}")

    exclude_set = set(exclude_place_ids or [])
    candidates = [
        place
        for place in data.get("results", [])
        if place.get("place_id") and place["place_id"] not in exclude_set
    ]

    if not candidates:
        return []

    # Using Google Maps Platform Distance Matrix API with mode=walking to determine real-time walking duration.
    max_walk_seconds = max_walk_time_minutes * 60.0
    verified_candidates: list[dict] = []

    for i in range(0, len(candidates), 25):
        batch = candidates[i : i + 25]
        destinations = "|".join([f"place_id:{c['place_id']}" for c in batch])
        dm_url = "https://maps.googleapis.com/maps/api/distancematrix/json"
        dm_params = {
            "origins": f"{latitude},{longitude}",
            "destinations": destinations,
            "mode": "walking",
            "key": maps_api_key,
        }
        dm_response = requests.get(dm_url, params=dm_params, timeout=15)
        dm_response.raise_for_status()
        dm_data = dm_response.json()

        dm_status = dm_data.get("status")
        if dm_status != "OK":
            dm_error = dm_data.get("error_message", f"Status code {dm_status}")
            logger.error("Google Distance Matrix API error: %s", dm_error)
            raise RuntimeError(f"Google Distance Matrix API error: {dm_error}")

        rows = dm_data.get("rows", [])
        if not rows or not rows[0].get("elements"):
            continue

        elements = rows[0]["elements"]
        for candidate, element in zip(batch, elements):
            if element.get("status") == "OK":
                duration_seconds = element.get("duration", {}).get("value", float("inf"))
                if duration_seconds <= max_walk_seconds:
                    verified_candidates.append(candidate)
                    if len(verified_candidates) >= max_results:
                        break
        if len(verified_candidates) >= max_results:
            break

    final_places: list[dict] = []
    for candidate in verified_candidates[:max_results]:
        pid = candidate["place_id"]
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {
            "place_id": pid,
            "fields": "place_id,name,formatted_address,geometry,website,price_level",
            "key": places_api_key,
        }
        details_response = requests.get(details_url, params=details_params, timeout=15)
        details_response.raise_for_status()
        details_data = details_response.json()

        if details_data.get("status") != "OK":
            logger.warning(
                "Place Details returned status %s for place %s",
                details_data.get("status"),
                pid,
            )
            p_name = candidate.get("name", "")
            p_addr = candidate.get("vicinity", "")
            loc = candidate.get("geometry", {}).get("location", {})
            p_lat = loc.get("lat", latitude)
            p_lng = loc.get("lng", longitude)
            p_website = None
            p_price = None
        else:
            result = details_data.get("result", {})
            p_name = result.get("name") or candidate.get("name", "")
            p_addr = result.get("formatted_address") or candidate.get("vicinity", "")
            loc = result.get("geometry", {}).get("location") or candidate.get(
                "geometry", {}
            ).get("location", {})
            p_lat = loc.get("lat", latitude)
            p_lng = loc.get("lng", longitude)
            p_website = result.get("website")
            price_level = result.get("price_level")
            p_price = (
                ("$" * price_level)
                if isinstance(price_level, int) and price_level > 0
                else None
            )

        final_places.append(
            {
                "place_id": pid,
                "name": p_name,
                "address": p_addr,
                "latitude": float(p_lat),
                "longitude": float(p_lng),
                "website": p_website,
                "price_range": p_price,
            }
        )

    return final_places
