"""The small, pinned CAD sidecar surface exposed by CAD2GIS."""
UPSTREAM_SHA = "cdb10638963898b3ea9b10cdd96a2c9bc495f184"
UPSTREAM_VERSION = "1.6.0"
UPSTREAM_REPOSITORY = "https://github.com/U-C4N/Autocad-MCP"
MAX_DXF_BYTES = 512 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
CALL_TIMEOUT = 120
ALLOWED_TOOLS = frozenset({
    "drawing_understand", "drawing_topology_check", "drawing_diff",
    "analysis_select_by_layer", "analysis_select_by_type", "entity_get",
    "layer_list", "layer_create", "entity_create_line", "entity_create_circle",
    "entity_create_arc", "entity_create_polyline", "entity_create_rectangle",
    "entity_create_text", "entity_delete",
})
MUTATING_TOOLS = frozenset({name for name in ALLOWED_TOOLS
                            if name.startswith("entity_create_")
                            or name in {"layer_create", "entity_delete"}})

