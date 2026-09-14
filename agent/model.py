import logging
import os
from google import genai
from strands.models.gemini import GeminiModel

logger = logging.getLogger(__name__)


class VertexGeminiModel(GeminiModel):
    """
    Strands-compatible model provider for Google Vertex AI Gemini.
    This is the ONLY LLM backend in the project — there is no fallback
    to any other provider. If the Vertex AI call fails, the exception
    propagates.
    """

    def __init__(self) -> None:
        client = genai.Client(
            vertexai=True,
            api_key=os.environ["GOOGLE_CLOUD_API_KEY"],
        )
        model_id = os.environ["GEMINI_MODEL_ID"]
        super().__init__(client=client, model_id=model_id)
