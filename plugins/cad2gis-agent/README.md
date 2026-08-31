# CAD2GIS Agent

CAD2GIS Agent is the thin, cross-agent registration layer for the canonical
CAD2GIS Python package. It adds one skill and one MCP server; it does not copy
reader, mapping, topology, length, CRS, or GeoPackage logic.

## Install the runtime

Use Python 3.11 or 3.12. Install the self-contained agent runtime from any
directory; do not use an editable worktree for a long-lived plugin process:

```shell
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis runtime install
cad2gis doctor --deep --strict --json
```

The agent extra uses platform wheels for GDAL/OGR and ezdxf. The runtime command
installs the checksum-pinned official LibreDWG release on Windows. On
macOS/Linux it uses Homebrew when available, then falls back to a rootless build
of the checksum-pinned official source release in the user cache. AutoCAD and
Conda are not required.

## Grant project access explicitly

The MCP service is fail-closed. Claude Code uses `CLAUDE_PROJECT_DIR`, while
Cursor and VS Code templates use `${workspaceFolder}`. Only set an override
when files intentionally live outside the active workspace:

```powershell
$env:CAD2GIS_PROJECT_ROOTS = "E:\projects;D:\survey-data"
```

```bash
export CAD2GIS_PROJECT_ROOTS="$HOME/projects:/srv/survey-data"
```

The separator is the operating system path separator (`;` on Windows, `:` on
POSIX). Plugin caches are never treated as project roots.

## Clients

The repository plugin works directly in Codex. For Claude Code, Cursor, VS
Code/GitHub Copilot, or a generic MCP host, copy the matching file from
[`clients`](clients). The templates contain no machine-specific path.

Every stdio registration resolves the installed `cad2gis-agent-mcp` console
entry point rather than a repository-relative Python file. This avoids drive,
user-name, shell, plugin-cache, and `python`/`python3` differences. If the
command is not found, install the runtime with the same Python environment used
by the host, then restart that host.

The recommended cross-platform runtime install is:

```text
uv tool install --python 3.12 --force ".[agent]"
```

From a machine without a checkout, install from the public source archive with
`uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"`.
The archive avoids cloning the repository history, and `--force` replaces an
older tool even when its editable source worktree no longer exists. Never use
`--editable` for a long-lived client runtime.
Confirm `cad2gis-agent-mcp` is on `PATH` before installing or enabling the
plugin. Plugin registration intentionally does not download Python packages or
silently select another interpreter.

Run `cad2gis runtime install` once after installation or from the MCP
`install_runtime` tool. Run `cad2gis doctor --deep --json` to inspect the
platform wheel and reader state; require `cad2gis doctor --deep --strict --json`
and `conversion_ready: true` before converting production drawings. The Conda
file in the source repository remains an optional native-development profile.

All stdio clients invoke the installed `cad2gis-agent-mcp` entry point. Local
HTTP uses `http://127.0.0.1:8768/mcp`; expose it beyond loopback only through an
authenticated TLS reverse proxy.

## First request

Ask the agent to call `get_capabilities` first. A correct run then inspects the
source, creates source-bound onboarding decisions, validates the project, runs
the canonical conversion, and reports geometry, topology, length, and
coordinate accuracy separately. It finishes with `audit_run`, which verifies
artifact hashes and compares the physical GeoPackage layer census with the run
manifest.

For GCP work, call `prepare_review_workspace` and open the returned URL. Web
edits stay in a revision store. Exporting GCPs returns a copyable command that
creates a new calibrated run; it does not mutate the existing GeoPackage.
If the source DWG is unavailable on the current machine, the GCP profile is
still preserved but the UI deliberately withholds an invalid rerun command.
Append `?demo=1` to the review URL for a clearly labeled, synthetic browser-only
walkthrough. Demo data never substitutes for a reader-backed conversion run.
