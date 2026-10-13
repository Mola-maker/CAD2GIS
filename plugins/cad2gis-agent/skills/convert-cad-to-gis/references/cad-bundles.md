# Optional CAD sidecar reference

Use this reference when the user requests CAD/DXF queries, drawing checks or
edits before GIS conversion. The optional engine runs locally in an isolated
environment; it edits workspace copies. It does not grant surveying accuracy.

1. Call `get_cad_bundle_status`. If not installed, explain the optional setup:
   `cad2gis cad-bundle install --prefix "<NEW_RUNTIME_PREFIX>"`.
   Set `CAD2GIS_CAD_BUNDLE_COMMAND` to the returned absolute executable path and
   restart the MCP client. Do not automatically install during ordinary conversion.
2. Call `prepare_cad_bundle_workspace` with `source` (existing DXF) and
   `workspace` (new directory), both inside authorized project roots. Keep the
   returned `workspace_id`. DWG must first be exported through the installed
   LibreDWG `dwg2dxf` reader; the headless sidecar cannot open DWG directly.
3. Call `list_cad_bundle_tools` with `workspace_id`. Use the returned schemas,
   not an upstream README tool count.
4. Call `call_cad_bundle_tool` with `workspace_id`, `tool_name` and `arguments`.
   For example, select `entity_create_line` with
   `{"x1":0,"y1":0,"x2":10,"y2":0,"layer":"CABLE"}`. The result includes
   `edited_source`, source hash, current hash and revision. Lifecycle/path
   parameters are managed internally; never send path, old_path or new_path.
5. Revalidate `edited_source` as a new source: call `inspect_source`, create a
   fresh project using `bootstrap_project`, prepare/review source-bound mappings
   and unit/CRS declarations, then `validate_project`, `run_conversion` and
   `audit_run`. Old source profiles and old hashes do not authorize the new copy.

Native DXF inspection shares the same entity parser as the LibreDWG DXF path.
Conversion still requires the GIS runtime and reviewed semantics. Failed or
timed-out edits keep the current revision. OSM or nominal CRS alignment cannot
be promoted to absolute accuracy by sidecar output.
