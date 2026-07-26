# CAD2GIS agent clients

CAD2GIS exposes one canonical MCP tool surface over both standard transports:

- `stdio` for a local agent that launches the process.
- Streamable HTTP at `http://127.0.0.1:8768/mcp` for local clients that connect
  to an already-running server.

Copy the matching template:

- Claude Code: merge `claude-code.mcp.json` into the project `.mcp.json`.
- Cursor: merge `cursor.mcp.json` into `.cursor/mcp.json`.
- VS Code / GitHub Copilot agent mode: copy `vscode.mcp.json` to
  `.vscode/mcp.json`.
- Codex: merge `codex.config.toml` into project `.codex/config.toml` or the
  user configuration and adjust the two absolute paths.

For any other MCP host, use the stdio command from these templates or start the
server command in `streamable-http.json` and connect to its `mcp_url`.

The HTTP server deliberately binds only to loopback and has no remote
authentication. Put an authenticated reverse proxy in front of it before any
network deployment.
