# CAD2GIS · CAD to GIS for AI agents

Convert DWG/DXF into inspectable GIS deliveries, with source hashes, entity provenance, geometry, topology and coordinate evidence. The CLI, Python API, review workspace and MCP plugin use the same conversion pipeline.

[中文](README.md) · [Official demo](https://cad2gis.molamaker.cn/) · [Nine drawing examples](https://mola-maker.github.io/CAD2GIS/deliveries/) · [Install](https://mola-maker.github.io/CAD2GIS/install.html) · [Releases](https://github.com/Mola-maker/CAD2GIS/releases)

| Capability | Requirements |
| --- | --- |
| DWG/DXF inspection | Managed LibreDWG for DWG; native DXF reader in CLI/API/MCP |
| GeoPackage conversion | Reviewed semantics, units and CRS; geometry/topology/length evidence |
| CAD editing through agents | Optional isolated MIT-licensed Autocad-MCP runtime; four MCP wrappers expose 15 verified tools |
| OSM/GCP alignment | Relative OSM matching and manual control points; absolute accuracy requires authoritative controls and independent checkpoints |
| QGIS delivery | GeoPackage, QGZ/QML, SVG comparison and verification records; independent rendering needs QGIS runtime |
| Agent clients | Codex/Claude Code repository marketplaces; Cursor/VS Code MCP configurations |

CAD edits create new DXF revisions without modifying the source. Edited drawings return to the canonical CAD2GIS acceptance pipeline. See [CAD bundles](docs/CAD_BUNDLES.md). Other upstream tools have not been verified through this adapter.

## Install

Install [uv](https://docs.astral.sh/uv/) and use Python 3.12:

```shell
uv tool install --python 3.12 --force "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/archive/refs/heads/main.zip"
cad2gis-agent-mcp --help
cad2gis doctor --deep --profile full --json
```

DWG conversion additionally needs `cad2gis runtime install`. Native DXF inspection does not require LibreDWG; GIS conversion still needs GIS runtime. Configure an authorized project root before starting MCP.

```shell
codex plugin marketplace add Mola-maker/CAD2GIS --ref main
codex plugin add cad2gis-agent@cad2gis
```

```shell
claude plugin marketplace add Mola-maker/CAD2GIS
claude plugin install cad2gis-agent@cad2gis-tools
```

For other clients see [MCP templates](plugins/cad2gis-agent/clients/README.md). Start with `get_capabilities`, `get_runtime_status` and `inspect_source`.

## Examples and releases

[![CAD and GIS delivery overlay](https://mola-maker.github.io/CAD2GIS/deliveries/drawing-01/process/source-delivery-overlay.png)](https://mola-maker.github.io/CAD2GIS/deliveries/)

The nine examples reuse the existing public gallery. Their status remains `CONDITIONAL`: absolute surveying accuracy has not been verified. A declared CRS or visually aligned overlay alone does not prove positional accuracy.

0.8.0 is the Python/Agent source release. Existing desktop downloads retain their actual versions: Windows 0.7.8, Linux 0.7.0-3 and macOS 0.7.0. See the [download matrix](README.md#下载与版本). Older binaries retain their original license and validation scope.

Original code in this release uses [Apache-2.0](LICENSE.md), permitting commercial use, modification and redistribution. Third-party components and drawing data retain their own rights: [NOTICE](NOTICE.md).

[Contribute](CONTRIBUTING.md) · [Report an issue](https://github.com/Mola-maker/CAD2GIS/issues) · [Discovery channels](docs/DISCOVERY_CHANNELS.md) · [Plugin guide](plugins/cad2gis-agent/README.md)
