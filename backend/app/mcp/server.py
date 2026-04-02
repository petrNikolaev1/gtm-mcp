"""MCP Server — uses official MCP Python SDK for proper protocol compliance."""
import json
import logging

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.requests import Request

from app.mcp.tools import TOOLS
from app.mcp.dispatcher import dispatch_tool

logger = logging.getLogger(__name__)

# Create MCP server instance
mcp_server = Server("mcp-leadgen")

# SSE transport — path is relative to mount point (/mcp), so just /messages
sse_transport = SseServerTransport("/messages")

# Token store: session_id → auth token (extracted from HTTP headers on POST)
_session_tokens: dict[str, str] = {}

# Per-session token for concurrent session isolation
# Uses MCP SDK's request_context to identify which session a tool call belongs to.
# Each SSE connection = one session. Token stored per session_id.
_session_user_tokens: dict[int, str] = {}  # session_object_id → token


@mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    tools = []
    for t in TOOLS:
        tools.append(Tool(
            name=t["name"],
            description=(t.get("description") or "")[:1024],
            inputSchema=t.get("inputSchema", {"type": "object", "properties": {}}),
        ))
    return tools


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    # Token from arguments (explicit) or from session-local store (set by login tool)
    token = arguments.pop("_token", None)
    if not token:
        try:
            ctx = mcp_server.request_context
            session_key = id(ctx.session)
            token = _session_user_tokens.get(session_key, '')
        except (LookupError, AttributeError):
            pass
    if not token:
        # Fallback: token captured from SSE URL (path or query string)
        token = _session_tokens.get("_latest", "")
    if not token:
        for sid, t in _session_tokens.items():
            if t:
                token = t
                break
    logger.info(f"call_tool({name}): token={'YES:'+token[:12] if token else 'NONE'}, "
                f"_session_tokens={list(_session_tokens.keys())}, "
                f"_session_user_tokens={len(_session_user_tokens)}")
    try:
        # Reset cost tracker per tool call
        from app.services.cost_tracker import reset_tracker, get_tracker
        reset_tracker()

        result = await dispatch_tool(name, arguments, token, None)

        # Track MCP protocol tokens (input args + output result)
        result_json = json.dumps(result, default=str)
        args_json = json.dumps(arguments, default=str)
        mcp_input_chars = len(args_json)
        mcp_output_chars = len(result_json)
        # ~4 chars per token estimate
        mcp_input_tokens = mcp_input_chars // 4
        mcp_output_tokens = mcp_output_chars // 4
        tracker = get_tracker()
        tracker.entries.append({
            "service": "mcp",
            "tool": name,
            "input_tokens": mcp_input_tokens,
            "output_tokens": mcp_output_tokens,
            "total_tokens": mcp_input_tokens + mcp_output_tokens,
            "input_chars": mcp_input_chars,
            "output_chars": mcp_output_chars,
            "cost_usd": 0,  # free for now
        })

        # Flush cost entries to DB
        if tracker.entries:
            try:
                from app.db.database import async_session_maker
                from app.models.usage import MCPUsageLog
                from app.models.user import MCPUser
                from sqlalchemy import select
                async with async_session_maker() as s:
                    # Resolve user_id from token
                    user_id = None
                    if token:
                        from app.auth.middleware import _hash_token
                        th = _hash_token(token)
                        from app.models.integration import MCPApiToken
                        row = (await s.execute(select(MCPApiToken.user_id).where(MCPApiToken.token_hash == th))).scalar()
                        user_id = row
                    for entry in tracker.entries:
                        s.add(MCPUsageLog(
                            user_id=user_id or 0,
                            action="api_cost",
                            tool_name=name,
                            extra_data=entry,
                        ))
                    await s.commit()
            except Exception as ce:
                logger.debug(f"Cost tracking flush failed: {ce}")

        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def handle_sse(request: Request):
    """SSE endpoint — Starlette Request wrapper for MCP SDK."""
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


async def handle_messages(request: Request):
    """Message endpoint — Starlette Request wrapper for MCP SDK."""
    await sse_transport.handle_post_message(
        request.scope, request.receive, request._send
    )
