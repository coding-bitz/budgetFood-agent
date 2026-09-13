from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent.orchestrator import build_agent

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> str:
    message = payload.get("prompt")
    session_id = payload.get("session_id")
    if not message or not session_id:
        raise ValueError("The payload must include 'prompt' and 'session_id'.")

    # Build a dedicated Agent instance per invocation to maintain isolated sessions
    agent = build_agent(session_id=session_id)
    response = agent(message)
    return str(response)


if __name__ == "__main__":
    app.run()
