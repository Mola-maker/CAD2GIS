from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(os.environ.get("CAD2GIS_PROJECT_ROOT", Path.cwd())).resolve()


def _text(content: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": content}]}


def _project_context() -> str:
    return "\n".join(
        [
            "Cad2GIS project context",
            "",
            f"Project root: {PROJECT_ROOT}",
            "Package: src/cad2gis",
            "CLI: cad2gis from src/cad2gis/cli.py",
            "QGIS plugin: qgis_plugin/",
            "Docs: README.md, docs/technical_plan.md, docs/verification_report.md",
            "",
            "Runtime rule: keep conversion deterministic. Use LLMs only for offline review, documentation, and mapping proposals.",
            "",
            "Pipeline: Ingest -> Profile -> Parse -> Classify -> Topology/Refine -> Network -> Georeference -> Warehouse -> Accuracy",
        ]
    )


def _command_recipes() -> str:
    return "\n".join(
        [
            "Cad2GIS command recipes",
            "",
            "Environment:",
            "  conda activate cad2gis",
            "  pip install -e .",
            "  cad2gis doctor",
            "",
            "Tests:",
            "  python -m pytest tests/ -q",
            "",
            "DS-04 pipeline:",
            "  python -c \"from cad2gis.pipeline import run; c,r=run('build/normalized/DS-04_comms.dxf', benchmark='src/cad2gis/verify/benchmark/ds04_surveyed.json', warehouse='build/DS04_comms_full.gpkg'); print(r.accuracy['overall'])\"",
            "",
            "Static demo:",
            "  python demo/gen_report.py",
            "",
            "Interactive demo:",
            "  python -m uvicorn demo.server.app:app --port 8000",
        ]
    )


def _artifact_inventory() -> str:
    paths = [
        "build/DS04_comms_full.gpkg",
        "build/accuracy_DS04_v2.json",
        "build/unconverted_evidence_DS04.json",
        "build/transform_record_DS04.json",
        "demo/index.html",
        "docs/technical_plan.md",
        "docs/verification_report.md",
    ]
    lines = ["Cad2GIS artifact inventory", ""]
    for rel in paths:
        path = PROJECT_ROOT / rel
        status = "present" if path.exists() else "missing"
        lines.append(f"{rel}: {status}")
    return "\n".join(lines)


TOOLS: dict[str, dict[str, Any]] = {
    "cad2gis_project_context": {
        "description": "Return concise Cad2GIS architecture and project constraints.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "cad2gis_command_recipes": {
        "description": "Return common Cad2GIS environment, test, pipeline, and demo commands.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "cad2gis_artifact_inventory": {
        "description": "Return presence/absence for documented Cad2GIS deliverable artifacts.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def _handle(method: str, params: dict[str, Any] | None) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "cad2gis-project", "version": "0.1.0"},
        }
    if method == "tools/list":
        return {
            "tools": [
                {"name": name, **spec}
                for name, spec in TOOLS.items()
            ]
        }
    if method == "tools/call":
        name = (params or {}).get("name")
        if name == "cad2gis_project_context":
            return _text(_project_context())
        if name == "cad2gis_command_recipes":
            return _text(_command_recipes())
        if name == "cad2gis_artifact_inventory":
            return _text(_artifact_inventory())
        raise ValueError(f"unknown tool: {name}")
    if method == "notifications/initialized":
        return None
    raise ValueError(f"unknown method: {method}")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            result = _handle(method, request.get("params"))
            if request_id is None:
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id") if "request" in locals() else None,
                "error": {"code": -32000, "message": str(exc)},
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
