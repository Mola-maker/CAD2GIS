# MCP client templates

Install `cad2gis[agent]`, copy the matching template, and restart the client.
Claude Code resolves `CLAUDE_PROJECT_DIR`; Cursor and VS Code resolve
`${workspaceFolder}`; Codex starts the server in the active project. No
machine-specific path replacement is required. Use a new agent conversation
after changing tools or skills.

The templates intentionally use the `cad2gis-agent-mcp` console entry point so
they are independent of repository location, user name, drive letter, shell,
and plugin cache path.

For the optional Redis source-query cache, set `CAD2GIS_REDIS_URL`,
`CAD2GIS_REDIS_CACHE_TTL_SECONDS` and `CAD2GIS_REDIS_TIMEOUT_MS` in the environment
of the MCP host, then restart its server. The Codex plugin and client template
explicitly forward these variables; they contain no stored credentials. Other
hosts must likewise pass these variables to the backend process according to
their environment policy. Use `debug_mcp` with runtime scope to verify the actual
server configuration and connectivity. Leaving Redis unconfigured preserves
direct SQLite queries. Android and HTTP clients use their server's cache and
must not receive Redis credentials.
