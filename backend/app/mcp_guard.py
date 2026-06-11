"""ASGI guard for the mounted MCP streamable HTTP app.

mcp==1.9.1 raises an unhandled pydantic ValidationError when a JSON-RPC
request carries an unrecognized ``method`` (e.g. a client-proprietary method
like ``ai.smithery/events/list``). That exception escapes the shared
``session_manager.run()`` task group, which then shuts down the whole
StreamableHTTP session manager — every subsequent request to ``/mcp`` returns
a bare "Internal Server Error" until the process is restarted.

This middleware inspects the JSON-RPC ``method`` of incoming POSTs before
they reach the MCP app and, if unrecognized, responds with a normal
JSON-RPC ``-32601 Method not found`` error instead of letting the SDK crash.
"""

import json
import logging
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("insidedcpulse")

_client_ip_var: ContextVar[str] = ContextVar("client_ip", default="unknown")


def get_client_ip() -> str:
    """Return the client IP captured for the current /mcp request."""
    return _client_ip_var.get()


def _extract_client_ip(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    forwarded = headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    if client:
        return client[0]
    return "unknown"


# All `method: Literal[...]` values across mcp.types request/notification models.
VALID_METHODS = {
    "completion/complete",
    "initialize",
    "logging/setLevel",
    "notifications/cancelled",
    "notifications/initialized",
    "notifications/message",
    "notifications/progress",
    "notifications/prompts/list_changed",
    "notifications/resources/list_changed",
    "notifications/resources/updated",
    "notifications/roots/list_changed",
    "notifications/tools/list_changed",
    "ping",
    "prompts/get",
    "prompts/list",
    "resources/list",
    "resources/read",
    "resources/subscribe",
    "resources/templates/list",
    "resources/unsubscribe",
    "roots/list",
    "sampling/createMessage",
    "tools/call",
    "tools/list",
}


class MCPMethodGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            _client_ip_var.set(_extract_client_ip(scope))

        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return

        body = b""
        messages = []
        while True:
            message = await receive()
            messages.append(message)
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        try:
            payload = json.loads(body)
        except ValueError:
            payload = None

        errors = _unknown_method_errors(payload)
        if errors is not None:
            await _send_error(send, errors)
            return

        queue = list(messages)

        async def replay_receive():
            if queue:
                return queue.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


def _unknown_method_errors(payload):
    items = payload if isinstance(payload, list) else [payload]
    if not items or not all(isinstance(item, dict) for item in items):
        return None

    unknown = [item for item in items if item.get("method") not in VALID_METHODS]
    if not unknown:
        return None

    for item in unknown:
        logger.warning("rejecting unrecognized MCP method: %r", item.get("method"))

    return [
        {
            "jsonrpc": "2.0",
            "id": item["id"],
            "error": {"code": -32601, "message": f"Method not found: {item.get('method')!r}"},
        }
        for item in items
        if "id" in item
    ]


async def _send_error(send: Send, errors: list) -> None:
    if not errors:
        status, body = 202, b""
    else:
        result = errors[0] if len(errors) == 1 else errors
        status, body = 200, json.dumps(result).encode()

    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json")],
    })
    await send({"type": "http.response.body", "body": body})
