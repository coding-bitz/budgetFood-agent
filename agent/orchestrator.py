import os
from strands import Agent
from strands.models import BedrockModel
from strands.session.s3_session_manager import S3SessionManager

from agent.hooks.search_limits import SearchLimitsHook
from agent.steering.grounding import grounding_handler
from agent.tools.budget import split_budget
from agent.tools.menu import read_web_menu
from agent.tools.places import find_nearby_places
from agent.tools.registry import log_result

SYSTEM_PROMPT = """You are BudgetFoodAgent. Your job is to decide what to eat within a budget
and nutritional criteria given by the user, using real data.

Rules you always follow:
1. You never propose a restaurant, dish, or price that you haven't obtained
   from a tool in this same conversation. If a tool gives you no data, you
   don't invent any.
2. You always show the filtering process: how many places you found, how
   many had a website, how many had a menu, and which ones you built the
   final proposal from.
3. If you don't find enough options, you call find_nearby_places again to
   request another batch before answering — the batch size and the radius
   are handled automatically, you don't need to compute them yourself. You
   never ask the user to hand you a menu.
4. You classify dishes against the user's nutritional criteria yourself,
   based on the dish names and descriptions returned by read_web_menu. If
   a dish's description isn't enough to decide, assume it does NOT meet
   the criteria and say so explicitly.
5. At the end of every query, you log the result with log_result.
6. You never carry out or suggest completing a purchase or order inside a
   delivery app. Your job ends at the proposal; the user decides where to
   order."""


def build_agent(session_id: str) -> Agent:
    model = BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        region_name=os.environ["AWS_REGION"],
    )
    session_manager = S3SessionManager(
        session_id=session_id,
        bucket=os.environ["SESSIONS_S3_BUCKET"],
        region_name=os.environ["AWS_REGION"],
    )
    return Agent(
        model=model,
        tools=[find_nearby_places, read_web_menu, split_budget, log_result],
        system_prompt=SYSTEM_PROMPT,
        hooks=[SearchLimitsHook()],
        plugins=[grounding_handler],
        session_manager=session_manager,
    )
