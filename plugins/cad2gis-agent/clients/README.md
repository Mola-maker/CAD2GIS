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
## QGIS runtime

For SVG/OSM publication and independent QGIS rendering, set
`CAD2GIS_QGIS_PYTHON` in the environment that launches the MCP server to the
local Python or launcher that can import `qgis.core`. The Codex allow-list
forwards this variable. Other stdio clients inherit their launching environment;
for streamable HTTP, configure it on the server. It is an executable path, not
a shell command. Missing QGIS does not disable source inspection or conversion.

`CAD2GIS_QGIS_FONT` may point to a local font file for headless QGIS processes.
The font is loaded only into that process; no system font settings are changed.
Review verification rejects missing fonts and unsupported rendered label glyphs.

## Optional CAD sidecar

Set `CAD2GIS_CAD_BUNDLE_COMMAND` to the absolute executable returned by
`cad2gis cad-bundle install --prefix "<NEW_PREFIX>"`. Restart the client, then
call `get_cad_bundle_status`. Codex forwards the variable explicitly; other
stdio hosts must inherit or pass it in their launch environment.

## MCPB 0.8.0

发行页的 `.mcpb` 通过 uv 下载 SHA-256 固定的新版 wheel。首次启动需要 uv、Python 3.12 与网络；必须选择授权项目目录，不能留空。插件包不含图纸。CAD 编辑运行时仍按旁路指南单独安装；GIS/DWG readiness 用 doctor 验证。
