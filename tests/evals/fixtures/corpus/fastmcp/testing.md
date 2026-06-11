# Testing FastMCP Servers

FastMCP servers are ordinary Python objects, which makes them testable without subprocesses, sockets, or fixtures that shell out to a host application.

## In-Memory Testing with the Client

The FastMCP `Client` accepts a server instance directly and talks to it over an in-memory transport:

```python
import pytest
from fastmcp import FastMCP, Client

mcp = FastMCP("Demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@pytest.mark.asyncio
async def test_add():
    async with Client(mcp) as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})
        assert result.content[0].text == "5"
```

Because no process boundary exists, tests run in milliseconds and stack traces from the server surface directly in the test failure.

## Testing Error Paths

Call a tool with arguments that violate its schema or trigger internal failures and assert on the error result rather than expecting an exception: tool execution errors arrive as results flagged `isError`, mirroring what a real client sees. Testing only happy paths hides exactly the failures models trigger most.

## Inspecting Registered Capabilities

`await client.list_tools()`, `list_resources()`, and `list_prompts()` return the generated definitions. Assert on the generated input schemas in at least one test — a renamed parameter or a lost type hint changes the schema and silently breaks model-side calling, and a schema assertion catches that at test time.
