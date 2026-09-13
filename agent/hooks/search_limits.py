import json
import logging
from threading import Lock

from strands.hooks import (
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

logger = logging.getLogger(__name__)

MAX_PLACES_PER_BATCH = 15
BATCH_MENU_THRESHOLD = 0.20
TARGET_MIN_PLACES_WITH_MENU = 10
TARGET_MAX_PLACES_WITH_MENU = 20
MAX_BATCHES = 6
RADIUS_INCREMENT_MILES = 2.0


def _extract_tool_result_json(event: AfterToolCallEvent) -> dict | list | None:
    """
    Extracts the JSON payload from a tool result, handling SDK structures.
    """
    result = event.result
    if result is None:
        logger.warning("Tool %s returned None result", event.tool_use.get("name"))
        return None

    if isinstance(result, (dict, list)):
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if isinstance(content, list) and len(content) > 0:
                block = content[0]
                if isinstance(block, dict) and "json" in block:
                    return block["json"]
                if isinstance(block, dict) and "text" in block:
                    try:
                        return json.loads(block["text"])
                    except (json.JSONDecodeError, TypeError):
                        pass
        return result

    logger.warning(
        "Could not parse result from tool %s: type=%s",
        event.tool_use.get("name"),
        type(result).__name__,
    )
    return None


class SearchLimitsHook(HookProvider):
    """
    Controls batch size, place deduplication, radius expansion, and safety limits.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._reset()

    def _reset(self) -> None:
        self.batches_run = 0
        self.total_places_with_menu = 0
        self.current_radius_miles = 5.0
        self.seen_place_ids: set[str] = set()
        self.places_data: dict[str, dict] = {}
        self._last_batch_size = 0
        self._last_batch_checked = 0
        self._last_batch_with_menu = 0

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeInvocationEvent, self._on_invocation_start)
        registry.add_callback(BeforeToolCallEvent, self._before_find_places)
        registry.add_callback(AfterToolCallEvent, self._after_tool_call)

    def _on_invocation_start(self, event: BeforeInvocationEvent) -> None:
        with self._lock:
            self._reset()

    def _before_find_places(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use.get("name") != "find_nearby_places":
            return

        with self._lock:
            if self.total_places_with_menu >= TARGET_MIN_PLACES_WITH_MENU:
                event.cancel_tool = (
                    f"You already have {self.total_places_with_menu} places with a "
                    f"confirmed menu (target: {TARGET_MIN_PLACES_WITH_MENU}-"
                    f"{TARGET_MAX_PLACES_WITH_MENU}). Don't request more batches — "
                    "build the proposal with what you already have."
                )
                return

            if self.batches_run >= MAX_BATCHES:
                event.cancel_tool = (
                    f"Search safety limit reached ({MAX_BATCHES} batches). "
                    f"Stop searching and build the proposal with the "
                    f"{self.total_places_with_menu} places with a confirmed menu "
                    "you already have, making clear in your response that "
                    "published-menu coverage in this area was low."
                )
                return

            tool_input = event.tool_use["input"]
            tool_input["max_results"] = MAX_PLACES_PER_BATCH
            tool_input["radius_miles"] = self.current_radius_miles
            tool_input["exclude_place_ids"] = list(self.seen_place_ids)

    def _after_tool_call(self, event: AfterToolCallEvent) -> None:
        tool_name = event.tool_use.get("name")
        if tool_name == "find_nearby_places":
            self._after_find_places(event)
        elif tool_name == "read_web_menu":
            self._after_read_menu(event)

    def _after_find_places(self, event: AfterToolCallEvent) -> None:
        result = _extract_tool_result_json(event)
        if result is None or not isinstance(result, list):
            logger.error("find_nearby_places returned an unparseable result")
            return

        new_place_ids = {p["place_id"] for p in result if p.get("place_id")}

        with self._lock:
            self.batches_run += 1
            new_count = len(new_place_ids - self.seen_place_ids)

            for p in result:
                pid = p.get("place_id")
                if pid and pid not in self.seen_place_ids:
                    self.places_data[pid] = {
                        "place_id": pid,
                        "name": p.get("name"),
                        "address": p.get("address"),
                        "has_website": p.get("website") is not None,
                        "website": p.get("website"),
                        "has_menu": False,
                        "menu_url": None,
                    }

            self.seen_place_ids |= new_place_ids
            self._last_batch_size = new_count
            self._last_batch_checked = 0
            self._last_batch_with_menu = 0

            if new_count == 0:
                self.current_radius_miles += RADIUS_INCREMENT_MILES

    def _after_read_menu(self, event: AfterToolCallEvent) -> None:
        result = _extract_tool_result_json(event)
        if result is None or not isinstance(result, dict):
            logger.error("read_web_menu returned an unparseable result")
            return

        has_menu = bool(result.get("has_menu"))
        menu_url = result.get("menu_url")

        tool_input = event.tool_use.get("input", {})
        place_id = tool_input.get("place_id")

        with self._lock:
            self._last_batch_checked += 1
            if has_menu:
                self._last_batch_with_menu += 1
                self.total_places_with_menu += 1

            if place_id and place_id in self.places_data:
                self.places_data[place_id]["has_menu"] = has_menu
                self.places_data[place_id]["menu_url"] = menu_url

            if (
                self._last_batch_size > 0
                and self._last_batch_checked >= self._last_batch_size
            ):
                batch_rate = self._last_batch_with_menu / self._last_batch_checked
                if batch_rate < BATCH_MENU_THRESHOLD:
                    self.current_radius_miles += RADIUS_INCREMENT_MILES
