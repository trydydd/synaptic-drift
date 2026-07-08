# The Context Object

FastMCP injects a `Context` object into any registered function that declares a parameter annotated with the `Context` type. The context is the function's gateway to session-scoped MCP capabilities: logging, progress reporting, resource access, and sampling.

## Getting a Context

Add a parameter with the `Context` annotation anywhere in the function signature:

```python
from fastmcp import FastMCP, Context

mcp = FastMCP("Demo")

@mcp.tool()
async def process(items: list[str], ctx: Context) -> str:
    await ctx.info(f"processing {len(items)} items")
    return "done"
```

The parameter is invisible to clients — it does not appear in the generated input schema — and FastMCP fills it in automatically on every call.

## Logging Through the Context

`ctx.debug()`, `ctx.info()`, `ctx.warning()`, and `ctx.error()` send MCP log notifications to the client at the corresponding severity. Prefer these over `print()` — on the stdio transport, printing to stdout corrupts the protocol stream, while context logging is always safe.

## Progress Reporting

Long-running tools should call `await ctx.report_progress(progress, total)` periodically. The client receives `notifications/progress` updates tied to the in-flight request and can render a progress bar. Send progress at meaningful milestones rather than every loop iteration to avoid flooding the session.

## Reading Resources from a Tool

A tool can read any resource its own server exposes via `await ctx.read_resource(uri)`. This is the supported way to share data between capabilities — for example, a `summarize_config` tool reading the `config://app` resource rather than re-parsing the file itself.

## Requesting Sampling

`await ctx.sample(messages)` issues a sampling request to the client, asking the host's model for a completion. The host may require user approval. Use it for small, bounded subtasks — classifying an input, drafting a summary — and always handle rejection, since users can deny any sampling request.
