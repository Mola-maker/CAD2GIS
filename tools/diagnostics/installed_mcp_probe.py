"""Exercise an installed CAD2GIS plugin through its declared MCP command."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _tool_payload(response: Any) -> dict[str, Any]:
    if bool(getattr(response, "isError", False)):
        raise RuntimeError(f"MCP tool returned an error: {response}")
    structured = getattr(response, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(response, "content", ()):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            payload = json.loads(text)
            if isinstance(payload, dict):
                return payload
    raise RuntimeError("MCP tool response did not contain a JSON object")


def _error_text(response: Any) -> str:
    return "\n".join(
        str(text)
        for item in getattr(response, "content", ())
        if isinstance((text := getattr(item, "text", None)), str)
    )


async def _probe(
    plugin_root: Path,
    sources: list[Path],
    *,
    inspect_sources: bool,
    bootstrap_root: Path | None,
    force_bootstrap: bool,
    validate_bootstraps: bool,
    prepare_ai: bool,
    prepare_existing_projects: list[Path],
    apply_existing_ai: bool,
    conversion_source: Path | None,
    conversion_project: Path | None,
    conversion_run: Path | None,
    inspect_run_dir: Path | None,
    rejection_project: Path | None,
    rejection_run_root: Path | None,
) -> dict[str, Any]:
    configuration = json.loads(
        (plugin_root / ".mcp.json").read_text(encoding="utf-8")
    )
    server = configuration["mcpServers"]["cad2gis"]
    environment = dict(os.environ)
    environment.update(
        {
            str(key): str(value)
            for key, value in server.get("env", {}).items()
        }
    )
    parameters = StdioServerParameters(
        command=str(server["command"]),
        args=[str(value) for value in server.get("args", ())],
        env=environment,
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = sorted(tool.name for tool in tools.tools)
            results = []
            if inspect_sources:
                for source in sources:
                    response = await session.call_tool(
                        "inspect_source",
                        {"source": str(source.resolve())},
                    )
                    results.append(_tool_payload(response))
            bootstraps = []
            if bootstrap_root is not None:
                for index, source in enumerate(sources):
                    slug = re.sub(
                        r"[^a-z0-9]+",
                        "-",
                        source.stem.casefold(),
                    ).strip("-")
                    project_dir = (
                        bootstrap_root / f"{index + 1:02d}-{slug}"
                    ).resolve()
                    response = await session.call_tool(
                        "bootstrap_project",
                        {
                            "source": str(source.resolve()),
                            "project_dir": str(project_dir),
                            "force": force_bootstrap,
                        },
                    )
                    bootstrap = _tool_payload(response)
                    if prepare_ai:
                        preparation_response = await session.call_tool(
                            "prepare_ai_onboarding",
                            {"project_dir": str(project_dir)},
                        )
                        bootstrap["ai_onboarding"] = _tool_payload(
                            preparation_response
                        )
                    if validate_bootstraps:
                        validation_response = await session.call_tool(
                            "validate_project",
                            {"project_dir": str(project_dir)},
                        )
                        bootstrap["validation"] = _tool_payload(
                            validation_response
                        )
                    bootstraps.append(bootstrap)
            existing_preparations = []
            if apply_existing_ai and len(prepare_existing_projects) != len(sources):
                raise ValueError(
                    "Applying AI onboarding requires one source per existing project"
                )
            for index, project_dir in enumerate(prepare_existing_projects):
                response = await session.call_tool(
                    "prepare_ai_onboarding",
                    {"project_dir": str(project_dir.resolve())},
                )
                preparation = _tool_payload(response)
                if apply_existing_ai:
                    suggestions = preparation[
                        "deterministic_role_suggestions"
                    ]
                    proposal = {
                        "schema_version": "cad2gis.ai_onboarding_proposal.v1",
                        "bundle_sha256": preparation["bundle_sha256"],
                        "source_sha256": preparation["source"]["sha256"],
                        "inventory_sha256": preparation["inventory_sha256"],
                        "crs_candidate_id": preparation[
                            "crs_candidates"
                        ][0]["candidate_id"],
                        "route_layers": suggestions["route_layers"],
                        "homepass_layers": suggestions["homepass_layers"],
                        "span_dimension_layers": suggestions[
                            "span_dimension_layers"
                        ],
                        "sling_wire_layers": suggestions["sling_wire_layers"],
                        "block_families": suggestions["block_families"],
                        "insert_layer_families": {
                            "BOITE": suggestions["boite_insert_layers"],
                            "PTECH": suggestions["ptech_insert_layers"],
                            "SITE": suggestions["site_insert_layers"],
                        },
                        "confidence": {"semantics": 0.9, "crs": 1.0},
                        "rationale": (
                            "Host AI accepted source-observed device, route, "
                            "annotation, and direct CAD metadata candidates."
                        ),
                    }
                    apply_response = await session.call_tool(
                        "apply_ai_onboarding",
                        {
                            "source": str(sources[index].resolve()),
                            "project_dir": str(project_dir.resolve()),
                            "proposal": proposal,
                            "proposer": {
                                "provider": "codex-host",
                                "model": "gpt-5",
                            },
                        },
                    )
                    preparation["application"] = _tool_payload(
                        apply_response
                    )
                existing_preparations.append(preparation)
            conversion = None
            if conversion_source is not None:
                if conversion_project is None or conversion_run is None:
                    raise ValueError(
                        "conversion_project and conversion_run are required "
                        "with conversion_source"
                    )
                response = await session.call_tool(
                    "run_conversion",
                    {
                        "source": str(conversion_source.resolve()),
                        "project_dir": str(conversion_project.resolve()),
                        "run_dir": str(conversion_run.resolve()),
                        "llm": "off",
                    },
                )
                conversion = _tool_payload(response)
            run_inspection = None
            if inspect_run_dir is not None:
                response = await session.call_tool(
                    "inspect_run",
                    {"run_dir": str(inspect_run_dir.resolve())},
                )
                run_inspection = _tool_payload(response)
            rejections = []
            if rejection_project is not None:
                if rejection_run_root is None:
                    raise ValueError(
                        "rejection_run_root is required with rejection_project"
                    )
                for index, source in enumerate(sources):
                    response = await session.call_tool(
                        "run_conversion",
                        {
                            "source": str(source.resolve()),
                            "run_dir": str(
                                (rejection_run_root / f"case-{index}").resolve()
                            ),
                            "project_dir": str(rejection_project.resolve()),
                            "llm": "off",
                        },
                    )
                    if not bool(getattr(response, "isError", False)):
                        raise RuntimeError(
                            "Cross-source project conversion unexpectedly succeeded"
                        )
                    rejections.append(
                        {
                            "source": str(source.resolve()),
                            "rejected": True,
                            "error": _error_text(response),
                        }
                    )
    return {
        "plugin_root": str(plugin_root),
        "tool_names": tool_names,
        "source_inspections": results,
        "project_bootstraps": bootstraps,
        "existing_ai_preparations": existing_preparations,
        "conversion": conversion,
        "run_inspection": run_inspection,
        "project_rejections": rejections,
    }


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    inventory = result.get("inventory", {})
    reader = result.get("reader", {})
    return {
        "schema_version": result.get("schema_version"),
        "source": result.get("source"),
        "inspection_status": result.get("inspection_status"),
        "reader": {
            key: reader.get(key)
            for key in (
                "backend",
                "total_rows",
                "parsed_rows",
                "skipped_rows",
                "completion_rows",
                "returned_records",
                "inventory_complete",
                "export_elapsed_seconds",
                "process_forced_after_export",
            )
        },
        "inventory": {
            key: inventory.get(key)
            for key in (
                "entity_count",
                "layer_count",
                "block_instances",
                "annotation_entities",
                "annotation_entities_with_text",
                "native_length_entities",
                "curve_facts_entities",
                "style_variants",
            )
        },
        "plan_domain": result.get("plan_domain"),
        "onboarding": result.get("onboarding"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--skip-inspection", action="store_true")
    parser.add_argument("--bootstrap-root", type=Path)
    parser.add_argument("--force-bootstrap", action="store_true")
    parser.add_argument("--validate-bootstraps", action="store_true")
    parser.add_argument("--prepare-ai", action="store_true")
    parser.add_argument(
        "--prepare-existing-project",
        action="append",
        type=Path,
        default=[],
    )
    parser.add_argument("--apply-existing-ai", action="store_true")
    parser.add_argument("--convert-source", type=Path)
    parser.add_argument("--convert-project", type=Path)
    parser.add_argument("--convert-run", type=Path)
    parser.add_argument("--inspect-run", type=Path)
    parser.add_argument("--assert-rejected-by-project", type=Path)
    parser.add_argument("--rejection-run-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    arguments = parser.parse_args()
    result = asyncio.run(
        _probe(
            arguments.plugin_root.expanduser().resolve(),
            [source.expanduser().resolve() for source in arguments.sources],
            inspect_sources=not arguments.skip_inspection,
            bootstrap_root=(
                None
                if arguments.bootstrap_root is None
                else arguments.bootstrap_root.expanduser().resolve()
            ),
            force_bootstrap=arguments.force_bootstrap,
            validate_bootstraps=arguments.validate_bootstraps,
            prepare_ai=arguments.prepare_ai,
            prepare_existing_projects=[
                value.expanduser().resolve()
                for value in arguments.prepare_existing_project
            ],
            apply_existing_ai=arguments.apply_existing_ai,
            conversion_source=(
                None
                if arguments.convert_source is None
                else arguments.convert_source.expanduser().resolve()
            ),
            conversion_project=(
                None
                if arguments.convert_project is None
                else arguments.convert_project.expanduser().resolve()
            ),
            conversion_run=(
                None
                if arguments.convert_run is None
                else arguments.convert_run.expanduser().resolve()
            ),
            inspect_run_dir=(
                None
                if arguments.inspect_run is None
                else arguments.inspect_run.expanduser().resolve()
            ),
            rejection_project=(
                None
                if arguments.assert_rejected_by_project is None
                else arguments.assert_rejected_by_project.expanduser().resolve()
            ),
            rejection_run_root=(
                None
                if arguments.rejection_run_root is None
                else arguments.rejection_run_root.expanduser().resolve()
            ),
        )
    )
    if arguments.summary:
        result["source_inspections"] = [
            _summary(item) for item in result["source_inspections"]
        ]
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if arguments.output is not None:
        destination = arguments.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
        print(str(destination))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
