"""Shared Prometheus registry + app-level metrics for the observability stack (spec 017).

Caddy already sees HTTP-level facts (status, handler, latency). The one thing it
*cannot* see is which MCP tool a `POST /mcp` invoked — every public MCP call looks
identical at the proxy. These metrics fill that gap from inside the app:

- `mcp_tool_calls_total{tool,outcome}` / `mcp_tool_duration_seconds{tool}` — recorded
  by the `_guard` decorator that already wraps every tool (mcp_server.py), so we get
  per-tool counts/latency for free, no JSON-RPC body parsing.
- `mcp_rate_limited_total` — bumped on the 429 path (mcp_ratelimit.py). No `ip` label
  on purpose: per-IP drill-down lives in the Caddy access logs (Loki), not in metrics,
  to keep cardinality bounded.

`/metrics` is scraped only on the internal compose network (never via Caddy/Funnel).
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Per-MCP-tool instrumentation (the granularity Caddy can't provide).
MCP_TOOL_CALLS = Counter(
    "mcp_tool_calls_total",
    "MCP tool invocations, by tool and outcome.",
    ["tool", "outcome"],  # outcome: ok | db_error | error
)
MCP_TOOL_DURATION = Histogram(
    "mcp_tool_duration_seconds",
    "MCP tool execution time in seconds, by tool.",
    ["tool"],
)

# Public-MCP rate-limit rejections. No `ip` label — see module docstring.
MCP_RATE_LIMITED = Counter(
    "mcp_rate_limited_total",
    "Requests rejected by the public /mcp rate limiter (HTTP 429).",
)


async def metrics_asgi_app(scope, receive, send):
    """Minimal ASGI endpoint exposing the default Prometheus registry.

    Used to mount `/metrics` onto the MCP ASGI chain. We hand-roll it instead of
    `prometheus_client.make_asgi_app()` only to avoid pulling its extra plumbing;
    the output is identical text exposition format.
    """
    body = generate_latest()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", CONTENT_TYPE_LATEST.encode())],
    })
    await send({"type": "http.response.body", "body": body})
