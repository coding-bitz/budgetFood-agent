from agent.hooks.search_limits import (
    MAX_BATCHES,
    MAX_PLACES_PER_BATCH,
    RADIUS_INCREMENT_MILES,
    TARGET_MIN_PLACES_WITH_MENU,
    SearchLimitsHook,
)
from strands.hooks import AfterToolCallEvent, BeforeInvocationEvent, BeforeToolCallEvent


def test_search_limits_hook_initial_state_and_reset():
    hook = SearchLimitsHook()
    hook.batches_run = 3
    hook.current_radius_miles = 9.0
    hook.seen_place_ids.add("p1")

    ev = BeforeInvocationEvent(agent=None, invocation_state={})
    hook._on_invocation_start(ev)

    assert hook.batches_run == 0
    assert hook.current_radius_miles == 5.0
    assert len(hook.seen_place_ids) == 0
    assert hook.total_places_with_menu == 0


def test_search_limits_hook_overrides_tool_input():
    hook = SearchLimitsHook()
    hook.seen_place_ids = {"place_1", "place_2"}
    hook.current_radius_miles = 7.0

    tool_use = {
        "name": "find_nearby_places",
        "input": {
            "latitude": 40.7128,
            "longitude": -74.0060,
            "max_results": 50,  # Model attempt to alter
            "radius_miles": 20.0,
        },
        "toolUseId": "call_1",
    }
    event = BeforeToolCallEvent(
        agent=None, selected_tool=None, tool_use=tool_use, invocation_state={}
    )
    hook._before_find_places(event)

    assert tool_use["input"]["max_results"] == MAX_PLACES_PER_BATCH
    assert tool_use["input"]["radius_miles"] == 7.0
    assert set(tool_use["input"]["exclude_place_ids"]) == {"place_1", "place_2"}
    assert event.cancel_tool is False


def test_search_limits_hook_cancels_when_target_reached():
    hook = SearchLimitsHook()
    hook.total_places_with_menu = TARGET_MIN_PLACES_WITH_MENU

    tool_use = {
        "name": "find_nearby_places",
        "input": {"latitude": 40.7128, "longitude": -74.0060},
        "toolUseId": "call_2",
    }
    event = BeforeToolCallEvent(
        agent=None, selected_tool=None, tool_use=tool_use, invocation_state={}
    )
    hook._before_find_places(event)

    assert event.cancel_tool is not False
    assert "already have 10 places" in event.cancel_tool


def test_search_limits_hook_cancels_when_max_batches_reached():
    hook = SearchLimitsHook()
    hook.batches_run = MAX_BATCHES

    tool_use = {
        "name": "find_nearby_places",
        "input": {"latitude": 40.7128, "longitude": -74.0060},
        "toolUseId": "call_3",
    }
    event = BeforeToolCallEvent(
        agent=None, selected_tool=None, tool_use=tool_use, invocation_state={}
    )
    hook._before_find_places(event)

    assert event.cancel_tool is not False
    assert "Search safety limit reached" in event.cancel_tool


def test_search_limits_hook_records_places_and_widens_radius():
    hook = SearchLimitsHook()
    assert hook.current_radius_miles == 5.0

    # Simulate find_nearby_places result with 5 places
    find_result = [
        {"place_id": f"pid_{i}", "name": f"Place {i}", "website": f"http://p{i}.com"}
        for i in range(5)
    ]
    find_tool_use = {"name": "find_nearby_places", "input": {}, "toolUseId": "call_4"}
    after_find_event = AfterToolCallEvent(
        agent=None,
        selected_tool=None,
        tool_use=find_tool_use,
        invocation_state={},
        result=find_result,
    )
    hook._after_find_places(after_find_event)

    assert hook.batches_run == 1
    assert len(hook.seen_place_ids) == 5

    # Simulate read_web_menu for each place where none has a published menu (0% < 20%)
    for i in range(5):
        menu_tool_use = {
            "name": "read_web_menu",
            "input": {"place_id": f"pid_{i}"},
            "toolUseId": f"call_menu_{i}",
        }
        after_menu_event = AfterToolCallEvent(
            agent=None,
            selected_tool=None,
            tool_use=menu_tool_use,
            invocation_state={},
            result={"has_menu": False, "menu_url": None, "dishes": []},
        )
        hook._after_read_menu(after_menu_event)

    # Threshold was evaluated at batch end: radius increased by RADIUS_INCREMENT_MILES
    assert hook.current_radius_miles == 5.0 + RADIUS_INCREMENT_MILES
