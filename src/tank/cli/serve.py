"""tank serve — start the MCP server (stdio transport)."""

from __future__ import annotations

import click


@click.command()
def serve() -> None:
    """Start the Tank MCP server (stdio transport).

    The server opens .tank/index.db relative to the current working
    directory. Run from your project root, or set cwd in your MCP
    client config.
    """
    from tank.server import create_server

    create_server().run()
