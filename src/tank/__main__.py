"""Entry point for python -m tank.server."""

from tank.server import create_server

mcp = create_server()
mcp.run()
