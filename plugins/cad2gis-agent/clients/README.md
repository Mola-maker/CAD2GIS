# MCP client templates

Install `cad2gis[agent]`, copy the matching template, and restart the client.
Claude Code resolves `CLAUDE_PROJECT_DIR`; Cursor and VS Code resolve
`${workspaceFolder}`; Codex starts the server in the active project. No
machine-specific path replacement is required. Use a new agent conversation
after changing tools or skills.

The templates intentionally use the `cad2gis-agent-mcp` console entry point so
they are independent of repository location, user name, drive letter, shell,
and plugin cache path.
