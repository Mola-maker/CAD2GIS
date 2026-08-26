# CAD2GIS Agent

CAD2GIS Agent is the thin, cross-agent registration layer for the canonical
CAD2GIS Python package. It adds one skill and one MCP server; it does not copy
reader, mapping, topology, length, CRS, or GeoPackage logic.

## Install the runtime

Use Python 3.11 or 3.12. The pinned production environment is recommended:

```powershell
conda env create -f env/environment.yml
conda activate cad2gis
python -m pip install -e ".[mcp,review]"
cad2gis doctor --deep --strict --json
```

On Linux/macOS, use a supported LibreDWG/ODA reader. AutoCAD Core Console is a
Windows-only reader; this does not change the MCP protocol or delivery schema.

## Grant project access explicitly

The MCP service is fail-closed. Set one or more allowed roots before launch:

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
[`clients`](clients) and replace `<ABSOLUTE_PROJECT_ROOT>`.

Every stdio registration resolves the installed `cad2gis-agent-mcp` console
entry point rather than a repository-relative Python file. This avoids drive,
user-name, shell, plugin-cache, and `python`/`python3` differences. If the
command is not found, install the runtime with the same Python environment used
by the host, then restart that host.

The recommended cross-platform runtime install is:

```text
uv tool install --python 3.12 --force ".[mcp,review]"
```

From a machine without a checkout, install from the public repository with
`uv tool install --python 3.12 "cad2gis[mcp,review] @ git+https://github.com/Mola-maker/CAD2GIS.git"`.
Confirm `cad2gis-agent-mcp` is on `PATH` before installing or enabling the
plugin. Plugin registration intentionally does not download Python packages or
silently select another interpreter.

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
