"""Stable stdio entry point for Antigravity, Codex and other MCP clients."""
from pw_server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
