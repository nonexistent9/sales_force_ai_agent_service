import os, sys, time
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import (
    AgentStreamEvent, MessageDeltaChunk, ThreadMessage, ThreadRun, RunStep
)

load_dotenv()
PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]      # e.g. https://<your-project>.regions.ai.azure.com
MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4o")

client = AgentsClient(PROJECT_ENDPOINT, DefaultAzureCredential())

# 1) Create agent + thread, add a user message
agent = client.create_agent(model=MODEL, name="streamer", instructions="Be concise.")
thread = client.threads.create()
client.messages.create(thread.id, role="user", content="Explain streaming in one sentence.")

# 2) Start a run and stream events (Server-Sent Events under the hood)
with client.runs.stream(thread_id=thread.id, agent_id=agent.id) as stream:
    for event_type, event_data, _ in stream:
        if isinstance(event_data, MessageDeltaChunk):          # token/text deltas
            sys.stdout.write(event_data.text or "")
            sys.stdout.flush()
        elif event_type == AgentStreamEvent.DONE:
            print("\n[stream complete]")
            break
