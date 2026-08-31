# Agent Runtime Research Record

## Decision

CAD2GIS uses one installable `cad2gis[agent]` Python tool, a LibreDWG CLI
adapter, dynamic client workspaces, and explicit runtime diagnostics. AutoCAD,
Conda, an editable Git worktree, and a machine-specific project directory are
not default requirements.

## Integrated search findings

Research was performed through GitHub source/release inspection and Exa search.
Tavily was also queried, but its API returned HTTP 432 (plan/quota limit), so no
Tavily result was used as evidence.

| Question | Evidence | Product decision |
| --- | --- | --- |
| Portable DWG reader | [LibreDWG official repository](https://github.com/LibreDWG/libredwg) and its stable 0.14 release publish `dwg2dxf`; Windows x64 archive SHA-256 is pinned in code. | Convert DWG to transient DXF, then adapt it through ezdxf. |
| Portable GDAL/OGR | [pyramids-gis installation metadata](https://pypi.org/project/pyramids-gis/) publishes bundled wheels for CPython 3.11/3.12 on Windows, macOS, and manylinux x64/ARM64. | Activate its bundled `osgeo` runtime; keep the established OGR pipeline. |
| POSIX LibreDWG | [Homebrew's LibreDWG formula](https://formulae.brew.sh/formula/libredwg) publishes bottles for macOS and Linux x64/ARM64; the official 0.14 release also publishes a source archive with SHA-256 `cb6ee0b...18351`. | `runtime install` uses Homebrew where present, then performs a rootless build of the checksum-pinned official source into the user cache. |
| Client project root | [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp) documents project-scoped configuration and `CLAUDE_PROJECT_DIR`. | Use client workspace variables/cwd; retain env roots only as an explicit override. |
| Agent packaging skills | OpenAI's `cli-creator` and Anthropic's official `mcp-builder` / `mcp-integration` skills matched the CLI and MCP packaging work. | Installed for subsequent agent sessions; product code remains governed by repository tests. |

## Verified source sample

The official LibreDWG 0.14 Windows CLI converted the repository APD sample to a
temporary DXF. The adapter returned all 6,940 model-space entities, including
222 `INSERT` and 170 `DIMENSION` entities, exactly matching the existing
source-bound census. The intermediate DXF was removed after ingestion.

## Licensing boundary

LibreDWG and pyramids-gis are GPLv3-family dependencies. CAD2GIS downloads or
installs them as separately attributed runtime components and does not copy
their source into this repository. Distribution owners must review the combined
distribution obligations before publishing proprietary binaries or appliances.
