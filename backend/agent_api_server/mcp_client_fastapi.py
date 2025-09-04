
"""
Run:  uvicorn mcp_client_fastapi:app --port 8080 --reload
"""

import contextlib
import socket
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from starlette.responses import StreamingResponse
from pydantic import BaseModel
import json
import asyncio
import os
from dotenv import load_dotenv
from azure.identity.aio import (AzureDeveloperCliCredential,
                                DefaultAzureCredential,
                                AzureCliCredential,
                                get_bearer_token_provider)
from openai import AzureOpenAI, AsyncAzureOpenAI   
from mcp_client import MCPClient
from sse_bus import SESSIONS, sse_event, JSONRPC, publish_progress, publish_message, associate_user_session
from typing import Any, Dict, List
import sys
from sse_starlette.sse import EventSourceResponse


load_dotenv()
aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
if not aoai_endpoint:
    sys.exit("Please set AZURE_OPENAI_ENDPOINT in .env")

aoai_deployment  = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
if not aoai_deployment:
    sys.exit("Please set AZURE_OPENAI_DEPLOYMENT_NAME in .env. Supported model version is gpt-4o")

aoai_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")

mcp_endpoint = os.getenv("MCP_SERVER_ENDPOINT")
if not mcp_endpoint:
    sys.exit("Please set MCP_SERVER_ENDPOINT in .env")

aoai_credential =  AzureCliCredential() # login with azd login # DefaultAzureCredential()
token_provider = get_bearer_token_provider(aoai_credential, "https://cognitiveservices.azure.com/.default")
aoai_client = AsyncAzureOpenAI(azure_endpoint=aoai_endpoint, azure_ad_token_provider=token_provider,
                               api_version=aoai_api_version)
POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "v0.1")
 # Dapr endpoint
mcp_cli = MCPClient(mcp_endpoint=mcp_endpoint)

print(f"Starting FastAPI server on {POD} with revision {REV}")
print(f"Azure OpenAI Endpoint: {aoai_endpoint}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Do any initialization tasks here
    try:
        #mcp_cli.set_broadcast_session(session_id)
        #await mcp_cli.connect(session_id=session_id)
        pass
    except Exception as e:
        print(f"Error connecting to MCP: {e}")
        raise e
    finally:
        yield
    
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


@app.get("/status")
async def status(request: Request):
    return {"status": "ok"}

def _normalize_session_id(raw: str | None, default: str = "default") -> str:
    if not raw:
        return default
    return raw.split(",")[0].strip()

@app.get("/events")
async def sse_events(request: Request):
    sid = request.query_params.get("sid")  
    session_id = _normalize_session_id(sid)
    print(f"[SSE OPEN] session={session_id} pod={POD} rev={REV}", flush=True)
    session = await SESSIONS.get_or_create(session_id)

    async def event_stream():
        # flush headers immediately (APIM/ACA friendly)
        yield "event: open\ndata: {}\n\n"

        heartbeat_every = 1.0  # seconds
        while True:
            if await request.is_disconnected():
                break
            try:
                # wait up to heartbeat interval for next message
                #msg = await asyncio.wait_for(session.q.get(), timeout=heartbeat_every)
                try:
                    msg = session.q.get_nowait()
                    print(f"[MCP CLIENT SSE YIELD] session={session_id} msg={msg}...", flush=True)
                except asyncio.QueueEmpty:
                    yield "event: noevent\ndata: {}\n\n"
                    await asyncio.sleep(heartbeat_every)
                    continue
                #msg = sse_event(payload, event="assistant")
                print(f"[SSE YIELD] {msg}")
                yield msg
                #await asyncio.sleep(5)
                #session.q.task_done()
            except asyncio.TimeoutError:
                # heartbeat (SSE comment doesn't disturb clients)
                yield ": ping\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


system_message = """You are a Sales Assistant at Lumeo an AI Company. You need to answer the user's questions about Sales Opportunities, Contacts and Accounts.
                The sales data is available in Sales Force. You are provided with simple-salesforce API to query sales force based on user question.
                Use the provided sales force API tools to assist with your responses.
                Answer the questions as accurately as possible, and if you don't know the answer, it's okay to say so.
                Answer only based on the information provided by the tool calls and nothing else. 
            """

class ConversationIn(BaseModel):
    user_query: str
    #client_id: str


async def call_mcp_tool(mcp_client, message):
    if getattr(message, "tool_calls", None):
        for tc in message.tool_calls:
            tool_name = tc.function.name
            tool_args = json.loads(tc.function.arguments)
            
            print(f"Calling tool: {tool_name} with args: {tool_args}")

            result = await mcp_client.session.call_tool(tool_name, tool_args)
            return result, tool_name, tool_args, tc.id
    return None, None, None, None




class SessionManager:
    """Keeps per-session, per-user chat histories."""
    def __init__(self) -> None:
        # sessions[session_id][user_id] -> list of messages (dicts or strings)
        self.sessions: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def get_history(self, session_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self.sessions.setdefault(session_id, {}).setdefault(user_id, [])

    def append(self, session_id: str, user_id: str, role: str, content: str) -> None:
        self.get_history(session_id, user_id).append({"role": role, "content": content})


# single, long-lived manager you reuse (e.g., module-level or injected)
session_manager = SessionManager()

async def handle_user_query(user_id: str, user_query: str, session_id: str) -> Dict[str, Any]:
    # Connect MCP
    #mcp_cli = MCPClient(mcp_endpoint=mcp_endpoint)
    mcp_cli.set_broadcast_session(session_id)
    await mcp_cli.connect(session_id=session_id)
    #with contextlib.suppress(Exception):
    #    await mcp_cli.connect(session_id=session_id)

    #try:
    # Build available tool schema for the model
    available_tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.inputSchema,
            },
        }
        for t in mcp_cli.mcp_tools.tools
    ]
    print("Available tools:", available_tools)

    # Build message list from stored history + current user input
    history = session_manager.get_history(session_id, user_id)
    system_msg = {"role": "system", "content": system_message.format(user_id=user_id)}
    msgs: List[Dict[str, Any]] = [system_msg, *history, {"role": "user", "content": user_query}]

    # First LLM call
    response = await aoai_client.chat.completions.create(
        model=aoai_deployment,
        messages=msgs,
        tools=available_tools,
        # Azure OpenAI Chat Completions uses `max_tokens`
        max_tokens=4000,
    )

    choice = response.choices[0]
    message = choice.message

    # Persist the user message once
    session_manager.append(session_id, user_id, "user", user_query)

    # Collect assistant text outputs (across potential tool call turns)
    final_text: List[str] = []

    # Safety: cap iterative tool-call loop
    for _ in range(16):
        # If no tool calls, this is a final assistant message; store and break
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            # message may be a dict or an SDK object; normalize
            content = (
                message.get("content")
                if isinstance(message, dict)
                else getattr(message, "content", None)
            )
            if content:
                final_text.append(content)
                session_manager.append(session_id, user_id, "assistant", content)
            break

        # Otherwise, execute the tool(s) one-by-one (or your call_mcp_tool batches them)
        result, tool_name, tool_args, tc_id = await call_mcp_tool(mcp_cli, message)
        if result is None:
            # Model asked for a tool but we couldn’t execute; surface what we have and stop
            content = (
                message.get("content")
                if isinstance(message, dict)
                else getattr(message, "content", None)
            )
            if content:
                final_text.append(content)
                session_manager.append(session_id, user_id, "assistant", content)
            break

        # Feed the tool result back
        # Ensure we keep using the same `msgs` list (not an undefined `messages`)
        msgs.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": getattr(result, "content", str(result)),
                },
            ]
        )

        follow_up = await aoai_client.chat.completions.create(
            model=aoai_deployment,
            messages=msgs,
            tools=available_tools,
            max_tokens=4000,
        )
        follow_up_choice = follow_up.choices[0]
        message = follow_up_choice.message

    print(final_text)
    return {"llm_response": final_text}

    #finally:
        # Optional: close MCP connection if your client needs explicit cleanup
        #with contextlib.suppress(Exception):
        #    await mcp_cli.close()
    

@app.post("/conversation/{user_id}")
async def start_conversation(user_id: str, convo: ConversationIn,  request: Request):
    sid = request.query_params.get("sid")  
    ui_session = _normalize_session_id(sid)
    associate_user_session(user_id, ui_session)
    result = await handle_user_query(user_id, convo.user_query, ui_session)
    return result
   

   





