# CAD2GIS Wiki · From drawings to reviewable GIS delivery

[中文](Home) · [Official architecture report](https://cad2gis.molamaker.cn/) · [Styled bilingual guide](https://mola-maker.github.io/CAD2GIS/wiki/en.html) · [Install and releases](https://github.com/Mola-maker/CAD2GIS/releases/tag/v0.8.0-site.20261013)

CAD2GIS converts DWG/DXF drawings into GIS deliverables with layers, declared coordinates, validation results and source evidence. MCP exposes local conversion, inspection and review tools to agents. The plugin supplies workflow instructions, skills and client configuration. Together they support a practical cycle: understand a drawing, edit a copy, convert it, inspect the result and prepare a QGIS delivery with records another person can review.

The current Agent release is **0.8.0**; original code is licensed under **Apache-2.0**. The website retains the report, fonts and CSS from the owner's local production archive. Its architecture report and eleven-chapter manual are historical snapshots: their 0.7.x desktop installers and experimental figures are not new Agent validation results. This Wiki describes the current MCP and plugin.

## Project value: deliver work that another person can inspect

| User problem | CAD2GIS capability | Practical benefit |
| --- | --- | --- |
| A drawing opens, but GIS layers, properties and coordinates are hard to hand over | DWG/DXF input, GIS conversion and QGIS delivery workflows | Turn visual drawings into data that can be reviewed and analyzed |
| An agent produces a file without explaining its origin | Input SHA-256, run results, validation reports and revision records | Reviewers can inspect provenance and processing decisions |
| Editing overwrites the original and makes changes hard to trace | Original preservation, independent working copies and revision snapshots | Keep original evidence and compare edits |
| Each agent client needs different setup | Local stdio MCP, Codex/Claude Code plugins and Cursor/VS Code configurations | Reuse the same toolchain across supported clients |
| Visual alignment is mistaken for surveying acceptance | Separate validation status, coordinate declarations and independent absolute accuracy checks | Make the remaining acceptance work explicit |

## What MCP and the plugin each provide

MCP connects an agent to the local CAD2GIS runtime. The user selects an authorized `project_root`, and tools operate inside that project boundary with path validation. Conversion results and evidence stay in the local project. Initial installation downloads dependencies; some OSM workflows fetch online map data. Local execution does not mean every workflow is completely offline.

The plugin packages MCP configuration, task skills and workflow instructions. It helps an agent choose tools, interpret validation results, iterate on a review and prepare deliverables. Output quality still depends on source drawings, coordinate and control information, and the operator's review of the checks.

The current code registers **63 MCP interfaces**, including four CAD wrappers. The 15 sidecar tools are optional capabilities within those wrappers; the two counts must not be added as independent MCP interfaces. Core workflows cover drawing understanding, DWG/DXF conversion, project validation, review and QGIS delivery. An optional CAD runtime adds layer/entity queries, 2D editing, topology checks and revision comparisons. Public directory status is recorded in `registries/publication-status.json`; client compatibility does not itself mean acceptance into a client's official directory.

## Optional CAD tools: 15 tools exercised through the adapter

The sidecar uses MIT-licensed U-C4N/Autocad-MCP v1.6.0 at a fixed source revision, installed separately. Runtime separation avoids conflicting MCP SDK dependencies. The adapter currently allows these 15 tools. It does not claim support for every upstream tool, AutoCAD COM automation or 3D editing.

| Category | Current tools | Purpose |
| --- | --- | --- |
| Understanding and checks | `drawing_understand`, `drawing_topology_check`, `drawing_diff` | Inspect structure, connections and revision differences |
| Selection and queries | `analysis_select_by_layer`, `analysis_select_by_type`, `entity_get`, `layer_list` | Locate objects and read their properties |
| Layer management | `layer_create` | Create layers for revisions |
| 2D creation | `entity_create_line`, `entity_create_circle`, `entity_create_arc`, `entity_create_polyline`, `entity_create_rectangle`, `entity_create_text` | Add basic geometry and annotations |
| Copy cleanup | `entity_delete` | Remove specified entities in a working copy |

CAD2GIS exposes four wrapper tools: `get_cad_bundle_status`, `prepare_cad_bundle_workspace`, `call_cad_bundle_tool`, and `list_cad_bundle_tools`. Preparation returns a workspace_id; list the actual allowed tools and their argument schemas before executing queries or edits. The returned `edited_source` identifies the revised DXF. Inspect that file and bootstrap a fresh project, then run CAD2GIS conversion and validation. Wrappers cannot invoke arbitrary upstream tools or write arbitrary files outside the project root.

## A workflow from edit to delivery

1. Select the project root, check the environment and inspect available capabilities.
2. Read the drawing, identify layers and entities, and retain the original input.
3. Open a working copy through the wrappers, inspect it and make supported 2D edits.
4. Export the revised DXF and run CAD2GIS GIS conversion and validation.
5. Review CRS, layers, connections, warnings and status; resolve decisions requiring human judgment.
6. Deliver GIS/QGIS outputs with provenance. Add authoritative control points and independent checkpoints when absolute accuracy acceptance is required.

Example request: “Check fiber-line and endpoint connections within this project. Preserve the original, convert the revised DXF into a GeoPackage, and report validation status, input hashes and items needing human review.”

## Installing the current Agent

Use Python 3.12 and uv to install the released Agent:

```sh
uv tool install --python 3.12 "cad2gis[agent] @ https://github.com/Mola-maker/CAD2GIS/releases/download/v0.8.0/cad2gis-0.8.0-py3-none-any.whl"
cad2gis-agent-mcp --help
cad2gis cad-bundle status
```

Codex and Claude Code use the repository marketplaces. Cursor and VS Code use the supplied MCP configuration. Select your actual project path; do not reuse a sample path as a real project. See the [README](https://github.com/Mola-maker/CAD2GIS#readme) and [plugin guide](https://github.com/Mola-maker/CAD2GIS/tree/main/plugins/cad2gis-agent) for client setup.

Install the optional CAD runtime separately:

```sh
cad2gis cad-bundle install --prefix /absolute/path/to/cad-runtime
```

Set `CAD2GIS_CAD_BUNDLE_COMMAND` to the executable path reported by installation, then start the MCP client. Executable locations differ by platform. Core CAD-to-GIS workflows remain available without the sidecar. See [CAD_BUNDLES.md](https://github.com/Mola-maker/CAD2GIS/blob/main/docs/CAD_BUNDLES.md) for installation, the pinned revision and access boundaries. Historical Windows, macOS and Linux desktop packages retain their original versions; they were not renamed to 0.8.0.

## Validation evidence and the nine-drawing demonstration

The 0.8.0 core and Agent changes passed the complete Linux / Python 3.12 suite with **1661 passed and 19 skipped**. All 15 sidecar tools were actually invoked on synthetic DXF inputs, exercising source preservation, revised export and the return to GIS conversion. This scope does not guarantee every customer DWG, AutoCAD COM or 3D object.

The imported website already includes effects for nine main drawings and multiple partitions, first-open screenshots, SVG extraction and map previews. These resources are reused; a separate nine-drawing report ZIP is not uploaded. Explore [drawing and alignment effects](https://mola-maker.github.io/CAD2GIS/docs/osm-gcp.html), the [SVG gallery](https://mola-maker.github.io/CAD2GIS/docs/svg.html), and [map previews](https://mola-maker.github.io/CAD2GIS/docs/map.html). Historical cases remain **CONDITIONAL**, with `absolute_accuracy_verified=false`. OSM alignment and visual adjustment do not replace surveyed control points or independent accuracy acceptance.

## Licensing, intended use and participation

Apache-2.0 permits commercial use, modification and redistribution of original code and includes an explicit patent license. Follow LICENSE and NOTICE when distributing it. Third-party components, fonts and sample material retain their own terms; the original-code license must not be applied indiscriminately to dependencies or engineering data. The MIT CAD sidecar remains separate, and notices for components such as LibreDWG are retained.

CAD2GIS is useful for organizing infrastructure drawings into GIS layers, preparing reviewable QGIS delivery, and letting agents assist with layer inspection and supported 2D geometry edits. Projects requiring statutory surveying outputs or independently verified precision need their own control information and acceptance process.

Report anonymized reproduction steps, input formats, environment versions, validation status and expected behavior through [Issues](https://github.com/Mola-maker/CAD2GIS/issues). See [CONTRIBUTING](https://github.com/Mola-maker/CAD2GIS/blob/main/CONTRIBUTING.md). Contributions to translations, CAD adapters and validation rules, along with public demonstrations and real usage feedback, help other users evaluate the project.
