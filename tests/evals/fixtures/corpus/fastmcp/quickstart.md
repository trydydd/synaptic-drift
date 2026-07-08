# FastMCP Quickstart

FastMCP is the high-level Python interface for building MCP servers. It turns plain Python functions into protocol-compliant tools, resources, and prompts by reading their signatures, type hints, and docstrings — no schema authoring required.

## Creating a Server

Instantiate the server with a name, register capabilities with decorators, and start it:

```python
from fastmcp import FastMCP

mcp = FastMCP("Demo Server")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

if __name__ == "__main__":
    mcp.run()
```

Calling `mcp.run()` with no arguments starts the server on the stdio transport, which is what desktop hosts expect when they launch the script as a subprocess.

## Registering Tools

The `@mcp.tool()` decorator converts a function into an MCP tool. The input schema is generated from the parameter names and type hints, and the docstring becomes the tool description. To publish the tool under a name different from the Python function name, pass it explicitly: `@mcp.tool(name="search")`. The decorator also accepts a `description` override when the docstring is not suitable.

## Registering Resources

The `@mcp.resource()` decorator exposes a function as a resource at a fixed URI: `@mcp.resource("config://app")`. Including `{placeholders}` in the URI — for example `@mcp.resource("users://{user_id}/profile")` — registers a resource template instead, and the placeholder values are passed as function arguments when a client reads the expanded URI.

## Registering Prompts

The `@mcp.prompt()` decorator registers a prompt template. The function returns either a plain string, which becomes a single user message, or a list of message objects for multi-turn templates. Function parameters become the prompt's declared arguments.

## Async Functions

Both `def` and `async def` functions can be registered as tools, resources, or prompts. Use `async def` whenever the function performs I/O — HTTP calls, database queries, file reads — so a slow operation does not block the server's event loop.
