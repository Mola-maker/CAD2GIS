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


async def probe(output: Path, source_run: Path | None) -> dict:
    checkout = Path(__file__).resolve().parents[1]
    roots = [checkout, *([source_run.parent] if source_run else [])]
    environment = {**os.environ, "PYTHONPATH": str(checkout / "src"),
                   "CAD2GIS_PROJECT_ROOTS": os.pathsep.join(map(str, roots)),
                   "CAD2GIS_PROJECT_ROOT": str(checkout), "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "cad2gis.agent_mcp"], env=environment)
    calls = []
    with (output / "stderr.jsonl").open("w", encoding="utf-8") as log:
        async with stdio_client(params, errlog=log) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialize = await asyncio.wait_for(session.initialize(), 30)
                listing = await session.list_tools()
                schemas = [tool.model_dump(mode="json", exclude_none=True) for tool in listing.tools]

                async def call(name, arguments):
                    start = time.perf_counter()
                    response = await asyncio.wait_for(session.call_tool(name, arguments), 30)
                    calls.append({"tool": name, "is_error": bool(response.isError),
                                  "wire_result_bytes": len(response.model_dump_json(exclude_none=True).encode()),
                                  "wall_ms": (time.perf_counter() - start) * 1000})
                    return response

                identity = await call("debug_mcp", {})
                detail = identity.structuredContent
                assert not identity.isError and detail["status"] == "ok"
                assert detail["protocol_version"] == initialize.protocolVersion
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
    (output / "tool-schemas.json").write_text(json.dumps(schemas, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "PASS", "initialize": initialize.model_dump(mode="json", exclude_none=True),
            "identity": detail, "calls": calls,
            "tool_schema_file_sha256": hashlib.sha256((output / "tool-schemas.json").read_bytes()).hexdigest(),
            "scope": "actual stdio protocol, runtime identity, bounded calls and error recovery; no conversion mutation"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report = asyncio.run(probe(args.output_dir.resolve(), args.source_run.resolve() if args.source_run else None))
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(args.output_dir / "report.json")}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
