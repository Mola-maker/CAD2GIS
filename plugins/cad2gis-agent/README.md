# CAD2GIS Agent

CAD2GIS Agent is the thin, cross-agent registration layer for the canonical
CAD2GIS Python package. It adds one skill and one MCP server; it does not copy
reader, mapping, topology, length, CRS, or GeoPackage logic.

## Reverse conversion

The updated runtime exposes `reverse_inspect`, `reverse_convert`, and
`reverse_verify` for GeoPackage/GeoJSON-to-CAD delivery. DXF R2018 needs no GDAL;
optional DWG R2018 requires AutoCAD Core Console and fresh native binary readback.
Source-bound registration manifests can restore the original coordinate frame.
Separate `reverse_source_inspect`, `reverse_source_convert`, and
`reverse_source_verify` tools reproduce explicitly selected, fingerprint-bound
native primitives from a strict source snapshot without applying GIS edits.
Neither mode claims complete original CAD restoration or absolute accuracy.

The CLI also provides source-hash-bound parallel batch plans. See
[usage and failure recovery](../../docs/GIS_TO_CAD.md) and
[verification evidence](../../docs/GIS_TO_CAD_VERIFICATION.md).
These tools require the runtime built from this change; an older running MCP
process does not gain them merely by updating plugin documentation. Install the
reviewed runtime package and restart the client, without editing a plugin cache.

The reverse-system source is on feature branch `codex/gis-to-cad-20260912`.
Use that branch when building this implementation; the general `main.zip`
installation below is not the branch-specific installation command. To install
from the reviewed source checkout, run from its repository root:

```shell
uv tool install --python 3.12 --force ".[agent]"
```

The separately verified local wheel is also available on the validation machine:

```shell
uv tool install --python 3.12 --force "cad2gis[agent] @ file:///E:/branch_CAD2GIS/validation/reverse-full-20260912/dist-final/cad2gis-0.6.0-py3-none-any.whl"
```

The wheel still uses project version `0.6.0`; identify this local build by the
SHA-256 in the verification record, rather than by version alone. These install
commands can fetch missing dependencies; validation used existing dependencies
and an isolated local installation, without replacing a user's MCP runtime.

## Install the runtime

Use Python 3.11 or 3.12. Install the self-contained agent runtime from any
directory; do not use an editable worktree for a long-lived plugin process:

```shell
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis runtime install
cad2gis doctor --deep --strict --profile full --json
```

On Windows, stop CAD2GIS MCP clients and any `cad2gis review` process before a
forced reinstall. Otherwise the running Python process can lock the uv tool
directory and cause `os error 32`. Restart clients after installation.

The agent extra uses platform wheels for GDAL/OGR and ezdxf. The runtime command
installs the checksum-pinned official LibreDWG release on Windows. On
macOS/Linux it uses Homebrew when available, then falls back to a rootless build
of the checksum-pinned official source release in the user cache. Forward CAD
reading does not require AutoCAD or Conda. Reverse DWG writing specifically
requires the qualified AutoCAD Core Console backend.

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
`install_runtime` tool. Run `cad2gis doctor --deep --profile full --json` to inspect
the MCP entry point, review UI, platform wheel, and reader state; require
`cad2gis doctor --deep --strict --profile full --json` and
`selected_profile_ready: true` before converting production drawings. The Conda
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
