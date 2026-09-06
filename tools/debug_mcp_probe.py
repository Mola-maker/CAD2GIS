"""Capture an actual stdio handshake, schemas and bounded diagnostic calls."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(
    output: Path, source_run: Path | None, *, installed_runtime: bool = False,
    project_roots: list[Path] | None = None, call_timeout: float = 30,
) -> dict:
    checkout = Path(__file__).resolve().parents[1]
    roots = list(project_roots or ([output.parent] if installed_runtime else [checkout]))
    if source_run:
        roots.append(source_run.parent)
    roots = list(dict.fromkeys(path.resolve() for path in roots))
    environment = dict(os.environ)
    module_args = ["-m", "cad2gis.agent_mcp"]
    if installed_runtime:
        # Do not let either the normal Python import path or the legacy backend
        # override turn an installed-package check into another checkout run.
        environment.pop("PYTHONPATH", None)
        environment.pop("CAD2GIS_BACKEND_PATH", None)
        module_args.insert(0, "-I")
    else:
        environment["PYTHONPATH"] = str(checkout / "src")
    environment.update({
        "CAD2GIS_PROJECT_ROOTS": os.pathsep.join(map(str, roots)),
        "CAD2GIS_PROJECT_ROOT": str(roots[0]), "PYTHONIOENCODING": "utf-8",
    })
    params = StdioServerParameters(command=sys.executable, args=module_args, env=environment)
    calls = []
    with (output / "stderr.jsonl").open("w", encoding="utf-8") as log:
        async with stdio_client(params, errlog=log) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialize = await asyncio.wait_for(session.initialize(), 30)
                listing = await session.list_tools()
                schemas = [tool.model_dump(mode="json", exclude_none=True) for tool in listing.tools]
                (output / "tool-schemas.json").write_text(
                    json.dumps(schemas, ensure_ascii=False, indent=2), encoding="utf-8",
                )

                async def call(name, arguments):
                    start = time.perf_counter()
                    response = await asyncio.wait_for(session.call_tool(name, arguments), call_timeout)
                    calls.append({"tool": name, "is_error": bool(response.isError),
                                  "wire_result_bytes": len(response.model_dump_json(exclude_none=True).encode()),
                                  "wall_ms": (time.perf_counter() - start) * 1000})
                    return response

                identity = await call("debug_mcp", {})
                detail = identity.structuredContent
                (output / "identity.json").write_text(
                    json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                assert not identity.isError and detail["status"] == "ok"
                assert detail["protocol_version"] == initialize.protocolVersion
                runtime = detail["identity"]
                assert runtime["tool_count"] == len(schemas)
                required_tools = {
                    "debug_mcp", "query_source_entities", "get_evidence_node",
                    "preview_semantic_patch", "commit_semantic_patch", "compile_semantic_revision",
                }
                assert required_tools.issubset({tool["name"] for tool in schemas})
                if installed_runtime:
                    package_path = Path(runtime["package_path"]).resolve()
                    assert "site-packages" in package_path.parts, (
                        f"Installed probe imported a package outside site-packages: {package_path}"
                    )
                    assert package_path.is_relative_to(Path(sys.prefix).resolve()), (
                        "Installed package is outside the selected interpreter environment"
                    )
                    assert Path(runtime["python_executable"]).resolve() == Path(sys.executable).resolve()
                drift = await call("debug_mcp", {"expected_identity": {"package_version": "0.0.0"}})
                assert drift.structuredContent["status"] == "VERSION_DRIFT"
                outside = str(Path(checkout.anchor) / "cad2gis-outside-allowed-root" / "absent.json")
                denied = await call("get_evidence_node", {"graph_path": outside, "node_id": "absent"})
                assert denied.isError and "PATH_OUTSIDE_ROOT" in denied.content[0].text
                if source_run:
                    bad = await call("query_source_entities", {"run_dir": str(source_run), "limit": 999})
                    assert bad.isError and "INVALID_QUERY" in bad.content[0].text
                    page = await call("query_source_entities", {"run_dir": str(source_run), "limit": 50, "max_bytes": 8192})
                    assert not page.isError and calls[-1]["wire_result_bytes"] <= 8192
                    verified = await call("debug_mcp", {"scope": "artifacts", "run_dir": str(source_run)})
                    assert not verified.isError and verified.structuredContent["artifacts"]["status"] == "verified"
    return {"status": "PASS", "initialize": initialize.model_dump(mode="json", exclude_none=True),
            "identity": detail, "calls": calls,
            "launch": {"mode": "installed_runtime" if installed_runtime else "checkout",
                       "python_executable": sys.executable, "module_args": module_args,
                       "call_timeout_seconds": call_timeout,
                       "project_roots": list(map(str, roots)),
                       "package_path": runtime["package_path"]},
            "plugin_bundle_validation": {
                "status": runtime["plugin_bundle"]["status"],
                "host_loaded_skill": runtime["host_loaded_skill"],
                "note": (
                    "Wheel runtime does not include the client plugin manifests/prompts; "
                    "this probe validates the installed server, not the host-loaded plugin bundle."
                    if runtime["plugin_bundle"]["status"] == "not_packaged_with_runtime"
                    else "Server-visible bundled manifests and prompts are included in debug_mcp identity."
                ),
            },
            "tool_schema_file_sha256": hashlib.sha256((output / "tool-schemas.json").read_bytes()).hexdigest(),
            "scope": "actual stdio protocol, runtime identity, bounded calls and error recovery; no conversion mutation"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--installed-runtime", action="store_true",
                        help="Use this interpreter's installed package in isolated mode, without checkout overrides.")
    parser.add_argument("--project-root", action="append", type=Path, default=[],
                        help="Allowed server filesystem root; repeat for multiple roots.")
    parser.add_argument("--call-timeout", type=float, default=30,
                        help="Per-RPC timeout in seconds; raise explicitly for cold source-index creation.")
    args = parser.parse_args()
    if not 0 < args.call_timeout <= 3600:
        parser.error("--call-timeout must be greater than zero and at most 3600 seconds")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        report = asyncio.run(probe(
            args.output_dir.resolve(), args.source_run.resolve() if args.source_run else None,
            installed_runtime=args.installed_runtime, project_roots=args.project_root,
            call_timeout=args.call_timeout,
        ))
    except Exception as exc:
        report = {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc),
                  "mode": "installed_runtime" if args.installed_runtime else "checkout",
                  "evidence": "See captured identity.json, tool-schemas.json and stderr.jsonl when available."}
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.output_dir / "report.json")}, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
