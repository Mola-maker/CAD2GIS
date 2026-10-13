# Optional CAD tools for CAD2GIS

CAD2GIS can query, inspect and edit a DXF copy through
[U-C4N/Autocad-MCP](https://github.com/U-C4N/Autocad-MCP), then send the revised
source through the same GIS validation and delivery pipeline. This optional
integration is fixed to commit `cdb10638963898b3ea9b10cdd96a2c9bc495f184` (1.6.0),
uses its MIT-licensed headless ezdxf engine, and has an independent Python
environment. The main CAD2GIS environment keeps its existing MCP SDK.

## Install once

Use an installed CAD2GIS Python 3.11/3.12 runtime:

```shell
cad2gis cad-bundle install --prefix "<NEW_RUNTIME_PREFIX>" --json
```

The prefix must be new. Installation needs Git and internet access. The result
contains the executable path and identity receipt. Configure the executable:

```bash
export CAD2GIS_CAD_BUNDLE_COMMAND="<NEW_RUNTIME_PREFIX>/bin/autocad-mcp"
cad2gis cad-bundle status --json
```

Windows PowerShell uses the full absolute
`<NEW_RUNTIME_PREFIX>\Scripts\autocad-mcp.exe` path in
`$env:CAD2GIS_CAD_BUNDLE_COMMAND`. Restart the Agent's MCP client after changing
its environment. The optional sidecar is never automatically downloaded by
inspection, conversion or capability discovery.

## Agent workflow

| CAD2GIS tool | Purpose |
| --- | --- |
| `get_cad_bundle_status` | Verify optional installation and pinned identity |
| `prepare_cad_bundle_workspace(source, workspace)` | Create a new source-preserving DXF workspace |
| `list_cad_bundle_tools(workspace_id)` | Discover actual allowed tools and argument schemas |
| `call_cad_bundle_tool(workspace_id, tool_name, arguments)` | Query or edit the copy and record evidence |

The 15 allowed upstream tools cover drawing understanding, topology checks,
revision differences, entity/layer queries, layers and basic geometry/text
creation and entity deletion. Tool discovery advertises only the intersection
with the installed upstream, not its entire tool catalog. Paths, shell commands
and LISP are not exposed through this adapter.

All workspaces must be inside configured CAD2GIS project roots. Symlink paths
are rejected, and comma-containing paths are unsupported because the upstream
path allowlist is comma-separated. Each call has a 120-second timeout, source
DXF size is limited to 512 MiB, and results to 4 MiB. Failed calls do not publish
a new current revision. The API credentials of the main server are not passed
to the CAD process.

## Return the edited copy to GIS

Native DXF now uses the same source-bound parser as LibreDWG-converted DXF.
Inspect the returned `edited_source`, bootstrap a fresh project, review its
semantics and unit/CRS declarations, then convert to a new run and audit it.
Source profiles from the original drawing cannot be reused with a new hash.

```shell
cad2gis inspect "<EDITED_SOURCE.dxf>" --json
cad2gis bootstrap "<EDITED_SOURCE.dxf>" --project "<NEW_PROJECT>" --json
# Review the generated project declarations before conversion.
cad2gis convert "<EDITED_SOURCE.dxf>" --project "<NEW_PROJECT>" --run-dir "<NEW_RUN>" --json
```

DXF inspection does not require LibreDWG. DWG input still needs the managed
reader; export to DXF before creating a sidecar workspace. GIS conversion needs
the GIS runtime. The sidecar neither verifies surveyed coordinates nor changes
the meaning of a `CONDITIONAL` result. Live AutoCAD COM control and 3D operations
are outside this adapter's current verified surface.

## Inspect the integration evidence

```shell
python tools/verify_cad_bundle.py --workdir "<NEW_TEST_DIRECTORY>" --output "<REPORT.json>"
```

This uses an authored synthetic drawing, verifies the unchanged source hash,
edits a copy through real MCP, checks differences/topology and runs canonical
GIS conversion with an explicitly declared nominal CRS. It provides engineering
integration evidence; it does not test customer drawings or absolute accuracy.
