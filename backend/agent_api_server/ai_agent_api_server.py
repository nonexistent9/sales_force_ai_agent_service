"""
Run:  uvicorn ai_agent_api_server:app --port 8080 --reload
"""
import sys
from importlib.metadata import version, PackageNotFoundError
def _v(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"

print(">>> sys.executable =", sys.executable)
print(">>> azure-ai-agents =", _v("azure-ai-agents"))
print(">>> azure-ai-projects =", _v("azure-ai-projects"))


import socket
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
import json
import os
from dotenv import load_dotenv

from starlette.responses import StreamingResponse

# Azure AI Agents (async)
from azure.ai.agents.aio import AgentsClient

from azure.ai.agents.models import (AgentStreamEvent,
                                    MessageDeltaChunk, 
                                    McpTool, 
                                    SubmitToolApprovalAction, 
                                    RequiredMcpToolCall,
                                    ToolApproval)
from azure.identity.aio import DefaultAzureCredential
import asyncio

load_dotenv()

# -----------------------
# Globals / Settings
# -----------------------
POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "v0.1")
print(f"Starting FastAPI server on {POD} with revision {REV}")

PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
MODEL = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-4o")
AGENT_NAME = os.getenv("AI_AGENT_NAME", "sf-sales-agent")
AGENT_INSTRUCTIONS = os.getenv(
    "AGENT_INSTRUCTIONS",
    "You are a concise, helpful assistant.",
)


# globals
agents_client: AgentsClient | None = None
AGENT_NAME = os.getenv("AI_AGENT_NAME", "sf-sales-agent")
MODEL = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-4o")
PROJECT_ENDPOINT = os.environ["AZURE_AI_PROJECT_ENDPOINT"]
AGENT_INSTRUCTIONS = """You are a Sales Assistant at Lumeo an AI Company. You need to answer the user's questions about Sales Opportunities, Contacts and Accounts.
                The sales data is available in Sales Force. You are provided with simple-salesforce API to query sales force based on user question.
                Use the provided sales force API tools to assist with your responses.
                Answer the questions as accurately as possible, and if you don't know the answer, it's okay to say so.
                Answer only based on the information provided by the tool calls and nothing else. 
                """
_AGENT = None  # cache the agent object; no global agent_id


agents_client: AgentsClient | None = None
SESSION_THREADS: dict[str, str] = {}

# Cache the actual agent object (so we don't expose or manage agent_id globally)
_cached_agent = None

# MCP Tool Configuration
mcp_server_label = "anildwa_sf_mcp_server"
mcp_server_url = "https://anildwasfmcpserver.politebush-063ce327.westus.azurecontainerapps.io/mcp"

mcp_tool = McpTool(
    server_label=mcp_server_label,
    server_url=mcp_server_url,
    allowed_tools=[],

)

# -----------------------
# Helpers
# -----------------------
def _normalize_session_id(raw: str | None, default: str = "default") -> str:
    if not raw:
        return default
    return raw.split(",")[0].strip()


async def get_or_create_agent():
    """
    Return the agent object for AGENT_NAME.
    No globals with agent_id are exposed; we keep the object cached.
    """
    global _cached_agent, agents_client
    assert agents_client is not None

    if _cached_agent is not None:
        return _cached_agent

    # list_agents() is async-paged; iterate with 'async for'
    found = None
    async for a in agents_client.list_agents():
        if a.name == AGENT_NAME:
            found = a
            break

    if found:
        _cached_agent = found
        print(f"Found agent '{found.name}' ({found.id})")
        return _cached_agent

    # Create the agent if not found (adjust tools/resources here if needed)
    created = await agents_client.create_agent(
        model=MODEL,
        name=AGENT_NAME,
        instructions=AGENT_INSTRUCTIONS,
    )
    _cached_agent = created
    print(f"Created agent '{AGENT_NAME}' ({created.id})")
    return _cached_agent


# -----------------------
# FastAPI app + lifespan
# -----------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global agents_client, _AGENT
    cred = DefaultAzureCredential()
    agents_client = AgentsClient(endpoint=PROJECT_ENDPOINT, credential=cred)

    print(f"Initialized AgentsClient for project: {PROJECT_ENDPOINT}")
    print(f"Using model deployment: {MODEL}")
    # find by name (AsyncItemPaged requires async iteration)
    found = None
    async for a in agents_client.list_agents():
        if a.name == AGENT_NAME:
            found = a
            break

    if found:
        _AGENT = found
        print(f"Found agent '{found.name}' ({found.id})")
    else:
        _AGENT = await agents_client.create_agent(
            model=MODEL,
            name=AGENT_NAME,
            instructions=AGENT_INSTRUCTIONS,
            tools=mcp_tool.definitions
        )
        print(f"Created agent '{AGENT_NAME}' ({_AGENT.id})")

    try:
        yield
    finally:
        if agents_client:
            await agents_client.close()



app = FastAPI(lifespan=lifespan)

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost",
    "http://127.0.0.1",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------
# Schema
# -----------------------
class ConversationIn(BaseModel):
    user_query: str


# -----------------------
# Routes
# -----------------------
@app.get("/status")
async def status(_: Request):
    return {"status": "ok"}

import asyncio
from typing import Optional

def _coerce_text(x) -> str:
    """Best-effort string from SDK content items across shapes."""
    if x is None:
        return ""
    if isinstance(x, str):
        return x

    # Direct attrs on model objects
    for attr in ("text", "value", "content", "data"):
        v = getattr(x, attr, None)
        if isinstance(v, str):
            return v

    # Nested common shape: obj.text.value
    v = getattr(getattr(x, "text", None), "value", None)
    if isinstance(v, str):
        return v

    # Mapping-like (for the rare raw dict case)
    if isinstance(x, dict):
        v = x.get("text") or x.get("value")
        if isinstance(v, str):
            return v

    # Dump methods → dict
    for dump in ("model_dump", "to_dict", "dict"):
        if hasattr(x, dump):
            try:
                d = getattr(x, dump)()
                if isinstance(d, dict):
                    v = d.get("text") or d.get("value")
                    if isinstance(v, str):
                        return v
            except Exception:
                pass

    return str(x)


def _extract_message_text(msg) -> str:
    """Aggregate human-readable text from an assistant message."""
    chunks: list[str] = []
    parts = getattr(msg, "content", []) or []
    for p in parts:
        t = _coerce_text(p)
        if t:
            chunks.append(t)
    return "\n".join(chunks).strip()


async def _fetch_last_assistant_with_retry(agents_client, thread_id: str,
                                           attempts: int = 12, delay_s: float = 0.25) -> Optional[str]:
    """
    After DONE, the assistant message may not be visible immediately.
    Poll a few times with small delays; return the newest assistant text found.
    """
    for i in range(attempts):
        last_assistant_msg = None
        last_ts = None

        # Do not assume pager order; scan all messages we get in this page.
        async for m in agents_client.messages.list(thread_id=thread_id):
            if getattr(m, "role", None) == "assistant":
                # Prefer the latest by created time if available; else keep the last encountered
                ts = getattr(m, "created_at", None) or getattr(m, "timestamp", None)
                if last_ts is None or (ts is not None and ts >= last_ts):
                    last_assistant_msg = m
                    last_ts = ts

        if last_assistant_msg:
            text = _extract_message_text(last_assistant_msg)
            if text:  # found usable text
                return text

        # Not found yet; brief backoff
        await asyncio.sleep(delay_s)

    return None

async def handle_user_query(user_id: str, user_query: str, ui_session: str):
    assert agents_client is not None and _AGENT is not None

    # per-session thread
    thread_id = SESSION_THREADS.get(ui_session)
    if not thread_id:
        thread = await agents_client.threads.create()
        thread_id = thread.id
        SESSION_THREADS[ui_session] = thread_id

    await agents_client.messages.create(thread_id, role="user", content=user_query)

    async def sse_generator():
        """
        Start a streaming run but do NOT emit partial tokens.
        - Auto-approve MCP tool calls when the run requires action.
        - After the run completes, fetch the latest assistant message and emit it once.
        - On errors, emit a single 'error' event.
        """
        assert agents_client is not None and _AGENT is not None

        try:
            stream_cm = await agents_client.runs.stream(
                thread_id=thread_id,
                agent_id=_AGENT.id,
            )

            async with stream_cm as stream:
                # We suppress partial token deltas entirely.
                async for event_type, event_data, _ in stream:
                    # Handle tool approval when required
                    if event_type == AgentStreamEvent.THREAD_RUN_REQUIRES_ACTION:
                        try:
                            required_action = getattr(event_data, "required_action", None)
                            if isinstance(required_action, SubmitToolApprovalAction):
                                tool_calls = (required_action.submit_tool_approval.tool_calls) or []
                                approvals = []
                                for tc in tool_calls:
                                    if isinstance(tc, RequiredMcpToolCall):
                                        print(f"Approving tool call: {tc}")
                                        approvals.append(
                                            ToolApproval(
                                                tool_call_id=tc.id,
                                                approve=True,
                                                headers=mcp_tool.headers,
                                            )
                                        )
                                if approvals:
                                    await agents_client.runs.submit_tool_outputs(
                                        thread_id=thread_id,
                                        run_id=event_data.id,
                                        tool_approvals=approvals,
                                    )
                        except Exception as tool_err:
                            # Single error event; no further streaming
                            yield "event: error\n"
                            yield f"data: {json.dumps({'error': f'tool_approval_failed: {str(tool_err)}'})}\n\n"
                            return

                    # Forward stream-level errors as a single error event
                    if event_type == AgentStreamEvent.ERROR:
                        yield "event: error\n"
                        yield f"data: {json.dumps({'error': str(event_data)})}\n\n"
                        return

                    # Normal completion: fetch final assistant message and emit once
                    if event_type == AgentStreamEvent.DONE:
                        try:
                            final_text = await _fetch_last_assistant_with_retry(agents_client, thread_id)
                            if not final_text:
                                final_text = "[No assistant text content returned.]"
                        except Exception as fetch_err:
                            yield "event: error\n"
                            yield f"data: {json.dumps({'error': f'final_fetch_failed: {str(fetch_err)}'})}\n\n"
                            return

                        # Emit exactly one payload (no token streaming)
                        yield "data: " + json.dumps({"text": final_text}) + "\n\n"
                        return



                        

        except Exception as e:
            # Single terminal error
            yield "event: error\n"
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return




    return StreamingResponse(sse_generator(), media_type="text/event-stream")



@app.post("/conversation/{user_id}")
async def start_conversation(user_id: str, convo: ConversationIn, request: Request):
    sid = request.query_params.get("sid")
    ui_session = _normalize_session_id(sid)
    return await handle_user_query(user_id, convo.user_query, ui_session)
