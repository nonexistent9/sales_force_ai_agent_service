"""
Run:  uvicorn sf_mcp_server:app --port 3000 --reload
Description: FastAPI MCP server exposing Salesforce query tool
"""

import asyncio, json, uuid, socket, os, inspect
from typing import Annotated, Any, Optional, Dict
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
from contextlib import asynccontextmanager
from tools import REGISTERED_TOOLS, TOOL_FUNCS, tool
import json, base64
from sf_tools import async_query_salesforce, get_sf_object_info
from sse_bus import SESSIONS, sse_event, JSONRPC

POD = socket.gethostname()
REV = os.getenv("CONTAINER_APP_REVISION", "unknown")



# ───────────────── tools ─────────────────────────────────────
@tool
async def query_salesforce(soql: Annotated[str, "SOQL query"]) -> Annotated[dict, "query Result"]:
    return await async_query_salesforce(soql)

# Lifespan event to fetch Salesforce object info
@asynccontextmanager
async def lifespan(app: FastAPI):
    global contact_info, account_info, opportunity_info
    contact_info = await get_sf_object_info("Contact")
    account_info = await get_sf_object_info("Account")
    opportunity_info = await get_sf_object_info("Opportunity")
    query_salesforce.__doc__ = f"""
        The user input needs to be translated into a SOQL query for the below SalesForce entities:

        - Contact
            {contact_info}
        - Account
            {account_info}
        - Opportunity
            {opportunity_info}

        Queries Salesforce using the provided SOQL query.
        Example SOQL: "SELECT Id, FirstName, LastName, Email, Account.Name FROM Contact WHERE LastName = 'Doe'"
        """
    yield


app = FastAPI(lifespan=lifespan)





# ─────────────── call_tool wrapper ensures session_id injection ──────────────
async def call_tool(name: str, raw_args: dict, tasks: BackgroundTasks, session_id: str):
    
    print(f"call_tool: {name} args={raw_args} session={session_id}", flush=True)

    if name not in TOOL_FUNCS:
        return "Error: Tool not found"

    fn  = TOOL_FUNCS[name]
    sig = inspect.signature(fn)
    args = dict(raw_args)
    if "session_id" in sig.parameters:
        args["session_id"] = session_id
    result = await fn(**args) if inspect.iscoroutinefunction(fn) else fn(**args)
    return result

def _ensure_calltool_result(obj):
    if isinstance(obj, dict) and "content" in obj:
        return obj
    return {"content": [{"type": "text", "text": str(obj)}]}

def _normalize_session_id(raw: str | None, default: str = "default") -> str:
    if not raw:
        return default
    return raw.split(",")[0].strip()



# ───────────────── SSE channel ───────────────────────────────────────────────
@app.get("/mcp")
async def mcp_sse(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    print(f"[SSE OPEN] session={session_id} pod={POD} rev={REV}", flush=True)
    session = await SESSIONS.get_or_create(session_id)

    async def event_stream():
        # flush headers immediately (APIM/ACA friendly)
        yield "event: open\ndata: {}\n\n"

        heartbeat_every = 120.0  # seconds
        while True:
            if await request.is_disconnected():
                break
            try:
                # wait up to heartbeat interval for next message
                #msg = await asyncio.wait_for(session.q.get(), timeout=heartbeat_every)
                try:
                    msg = session.q.get_nowait()
                    print(f"[SSE YIELD] session={session_id} msg={msg}...", flush=True)
                except asyncio.QueueEmpty:
                    yield "event: heartbeat\ndata: {}\n\n"
                    await asyncio.sleep(heartbeat_every)
                    continue
                print(f"[SSE YIELD] session={session_id} msg={msg}...", flush=True)
                yield msg
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

# ───────────────── health check ─────────────────────────────────────────────
@app.get("/status")
async def status(request: Request):
    return {"status": "ok"}

# ───────────────── JSON-RPC handler ──────────────────────────────────────────
@app.post("/mcp")
async def mcp_post(req: Request, tasks: BackgroundTasks):
    req_json   = await req.json()
    raw        = req.headers.get("Mcp-Session-Id")
    session_id = _normalize_session_id(raw, default=str(uuid.uuid4()))
    # ensure session exists for any tool that will stream
    await SESSIONS.get_or_create(session_id)

    method = req_json.get("method")
    rpc_id = req_json.get("id")
    print(f"[POST] method={method} session={session_id} pod={POD} rev={REV}", flush=True)

    match method:
        case "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "fastapi-mcp", "version": "0.1"},
                "capabilities": {"tools": {"listChanged": True, "callTool": True}}, #{"listTools": True, "toolCalling": True, "sse": True},
            }

        case "ping" | "$/ping":
            result = {} #{"pong": True}

        case "workspace/listTools" | "$/listTools" | "list_tools" | "tools/list":
            result = {"tools": REGISTERED_TOOLS}

        case "tools/call" | "$/call":
            tool_name = req_json["params"]["name"]
            raw_args  = req_json["params"].get("arguments", {})
            raw_out   = await call_tool(tool_name, raw_args, tasks, session_id)
            result    = _ensure_calltool_result(raw_out)

        case _ if method in TOOL_FUNCS:
            raw_args = req_json.get("params", {})
            raw_out  = await call_tool(method, raw_args, tasks, session_id)
            result   = _ensure_calltool_result(raw_out)

        case _:
            if rpc_id is None:
                return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
            return JSONResponse(
                content={"jsonrpc": JSONRPC, "id": rpc_id,
                         "error": {"code": -32601, "message": "method not found"}},
                headers={"Mcp-Session-Id": session_id},
                background=tasks,
            )

    return JSONResponse(
        content={"jsonrpc": JSONRPC, "id": rpc_id, "result": result},
        headers={"Mcp-Session-Id": session_id},
        background=tasks,
    )

# ───────────────── session cleanup ───────────────────────────────────────────
@app.delete("/mcp")
async def mcp_delete(request: Request):
    session_id = _normalize_session_id(request.headers.get("Mcp-Session-Id"))
    if session_id:
        deleted = await SESSIONS.delete(session_id)
        return Response(status_code=204 if deleted else 404)
    return Response(status_code=404)