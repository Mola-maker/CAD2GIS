"""Portable, append-only batch conversion and delivery contracts."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "cad2gis.batch.v1"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema_version") != SCHEMA or not contract.get("drawings"):
        raise ValueError("Expected a non-empty cad2gis.batch.v1 contract")
    seen = set()
    for item in contract["drawings"]:
        identifier = item.get("id", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", identifier) or identifier in seen:
            raise ValueError(f"Invalid or duplicate drawing id: {identifier!r}")
        seen.add(identifier)
        if not re.fullmatch(r"[a-f0-9]{64}", item.get("source_sha256", "")):
            raise ValueError(f"Missing source SHA256: {identifier}")
        for key in ("source", "project"):
            value = item.get(key)
            if not isinstance(value, str) or not value or "\\" in value or ":" in value:
                raise ValueError(f"{identifier}: {key} must be a portable relative path")
            candidate = (path.parent / value).resolve()
            if not candidate.is_relative_to(path.parent.resolve()):
                raise ValueError(f"{identifier}: {key} escapes the input bundle")
    return contract


def prepare(inputs: Path, output: Path) -> dict:
    """Inventory a directory; projects are explicit review inputs, never inferred approval."""
    inputs = inputs.resolve()
    if output.resolve().parent != inputs:
        raise ValueError("Write the contract inside the input directory for portable paths")
    drawings = []
    for source in sorted(inputs.rglob("*")):
        if source.is_file() and source.suffix.lower() in {".dwg", ".dxf"}:
            sha = digest(source)
            identifier = "drawing-" + hashlib.sha256(source.relative_to(inputs).as_posix().encode()).hexdigest()[:12]
            drawings.append({"id": identifier, "source": source.relative_to(output.parent.resolve()).as_posix(),
                             "source_sha256": sha, "project": f"projects/{identifier}"})
    if not drawings:
        raise ValueError("No DWG/DXF inputs found")
    contract = {"schema_version": SCHEMA, "drawings": drawings}
    with output.open("x", encoding="utf-8") as stream:
        json.dump(contract, stream, ensure_ascii=False, indent=2)
    return contract


def write_index(output: Path, report: dict) -> None:
    rows = []
    cards = []
    for item in report["drawings"]:
        links = " ".join(f'<a href="{html.escape(value, quote=True)}">{html.escape(key)}</a>'
                         for key, value in item.get("links", {}).items())
        rows.append(f'<tr><td>{html.escape(item["id"])}</td><td>{html.escape(item["status"] + " / " + item.get("stage", "archived"))}</td>'
                    f'<td>{html.escape(item.get("error", ""))}</td><td>{links}</td></tr>')
        preview = item.get("preview")
        if preview:
            cards.append(f'<article><h2>{html.escape(item.get("name", item["id"]))}</h2>'
                         f'<p>{html.escape(item["status"])} · 原图与交付几何叠加，绝对 GCP 精度未验收</p>'
                         f'<a href="{html.escape(preview, quote=True)}"><img loading="lazy" style="width:100%;height:auto" '
                         f'src="{html.escape(preview, quote=True)}" alt="{html.escape(item["id"])} 源图与成果比较"></a><p>{links}</p></article>')
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>CAD2GIS 批次交付</title><style>body{font:16px/1.6 system-ui;margin:40px;'
            'background:#f5f7fa;color:#16334b}table{border-collapse:collapse;width:100%}'
            'td,th{padding:14px;border:1px solid #bbc8d5;text-align:left}a{color:#005ea8;margin-right:12px}'
            'td{overflow-wrap:anywhere}</style><h1>CAD2GIS 批次交付</h1>'
            '<p>每张图独立保留运行、来源与失败记录。CONDITIONAL 不代表工程验收通过；'
            '没有独立 GCP 时，叠图一致不能证明绝对定位精度。</p>'
            '<p><a href="batch-report.json">机器可读批次报告</a></p>'
            '<table><tr><th>图纸</th><th>状态</th><th>异常</th><th>交付 / 证据</th></tr>'
            + "".join(rows) + '</table>' + "".join(cards) + '</html>')
    if report.get("status") == "RUNNING":
        page = page.replace('<title>', '<meta http-equiv="refresh" content="10"><title>', 1)
    (output / "index.html").write_text(page, encoding="utf-8")


def run_batch(contract_path: Path, output: Path, *, timeout: int = 1800) -> dict:
    contract_path = contract_path.resolve()
    contract = load_contract(contract_path)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "batch-contract.json", contract)
    report = {"schema_version": "cad2gis.batch-report.v1", "contract_sha256": digest(contract_path),
              "platform": sys.platform, "drawings": [{"id": item["id"], "source_sha256": item["source_sha256"],
                  "status": "PENDING", "stage": "queued", "links": {}} for item in contract["drawings"]], "status": "RUNNING"}
    for item, record in zip(contract["drawings"], report["drawings"]):
        folder = output / item["id"]
        folder.mkdir()
        record["status"] = "RUNNING"
        write_json(output / "batch-report.json", report)
        write_index(output, report)
        record["timings_seconds"] = {}

        def execute(stage, command, log_name):
            record["stage"] = stage
            record["links"][stage + " log"] = f'{item["id"]}/{log_name}'
            write_json(output / "batch-report.json", report)
            write_index(output, report)
            started = time.monotonic()
            try:
                with (folder / log_name).open("w", encoding="utf-8") as log:
                    return subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            finally:
                record["timings_seconds"][stage] = round(time.monotonic() - started, 6)

        try:
            source = (contract_path.parent / item["source"]).resolve()
            project = (contract_path.parent / item["project"]).resolve()
            if digest(source) != item["source_sha256"]:
                raise ValueError("Source SHA256 mismatch; reviewed configuration was not replayed")
            if not project.is_dir():
                raise ValueError("Reviewed project missing; bootstrap and review before conversion")
            original_hashes = {p.relative_to(project).as_posix(): digest(p) for p in sorted(project.rglob("*")) if p.is_file()}
            shutil.copytree(project, folder / "project")
            project = folder / "project"
            record["project_files_sha256"] = {p.relative_to(project).as_posix(): digest(p) for p in sorted(project.rglob("*")) if p.is_file()}
            if original_hashes != record["project_files_sha256"]:
                raise ValueError("Reviewed project changed while taking the batch snapshot")
            extraction = execute("source-export", [sys.executable, "-m", "cad2gis", "export-source", str(source),
                    "--run-dir", str(folder / "source"), "--json"], "source-export.log")
            record["links"]["源提取日志"] = f'{item["id"]}/source-export.log'
            if extraction.returncode:
                raise RuntimeError(f"Source extraction exit {extraction.returncode}; inspect source-export.log")
            indexing = execute("source-index", [sys.executable, "-m", "cad2gis", "index-source", str(folder / "source"), "--json"], "source-index.log")
            if indexing.returncode:
                raise RuntimeError(f"Source indexing exit {indexing.returncode}; inspect source-index.log")
            command = [sys.executable, "-m", "cad2gis", "convert", str(source),
                       "--project", str(project), "--run-dir", str(folder / "run"), "--json"]
            process = execute("conversion", command, "conversion.log")
            record["links"]["转换日志"] = f'{item["id"]}/conversion.log'
            if process.returncode:
                raise RuntimeError(f"Conversion exit {process.returncode}; inspect conversion.log")
            audit = execute("visual-audit", [sys.executable, "-m", "cad2gis.visual_audit", "--source-run", str(folder / "source"),
                    "--run", str(folder / "run"), "--output", str(folder / "visual")], "visual-audit.log")
            record["audit_status"] = "EXECUTED" if audit.returncode == 0 else "FAILED"
            audit_report = folder / "visual" / "report.json"
            if audit_report.exists():
                audit_value = json.loads(audit_report.read_text(encoding="utf-8"))
                if audit.returncode == 0:
                    record["audit_status"] = audit_value.get("status", record["audit_status"])
                record["links"]["视觉审查报告"] = f'{item["id"]}/visual/report.json'
            manifest_path = folder / "run" / "run_manifest.json"
            run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            record["partition_audits"] = {}
            for partition in sorted(run_manifest.get("delivery_partitions", {})):
                if not re.fullmatch(r"[A-Za-z0-9_-]+", partition):
                    raise ValueError("Unsafe partition identifier in run manifest")
                partition_audit = execute("visual-audit-" + partition,
                    [sys.executable, "-m", "cad2gis.visual_audit", "--source-run", str(folder / "source"),
                     "--run", str(folder / "run"), "--partition", partition,
                     "--output", str(folder / "visual" / partition)], "visual-audit-" + partition + ".log")
                record["partition_audits"][partition] = "EXECUTED" if partition_audit.returncode == 0 else "FAILED"
                record["links"][partition + " 视觉审查"] = f'{item["id"]}/visual/{partition}/report.json'
                if partition_audit.returncode:
                    record["audit_status"] = "FAILED"
            record["stage"] = "delivery-package"
            write_json(output / "batch-report.json", report)
            write_index(output, report)
            from .delivery import package_delivery
            delivery = package_delivery(folder / "run", folder / "delivery", audit_dir=folder / "visual")
            record.update(status=delivery["run_status"], delivery=delivery)
            record["links"]["QGIS 交付包"] = f'{item["id"]}/delivery.zip'
            record["links"]["交付清单"] = f'{item["id"]}/delivery/delivery-manifest.json'
            if (folder / "visual" / "source-delivery-overlay.png").exists():
                record["preview"] = f'{item["id"]}/visual/source-delivery-overlay.png'
            record["stage"] = "finished"
        except Exception as exc:
            record.update(status="FAILED", error=str(exc), error_type=type(exc).__name__)
        write_json(folder / "result.json", record)
        write_json(output / "batch-report.json", report)
        write_index(output, report)
    report["status"] = "FAILED" if any(r["status"] == "FAILED" or r.get("audit_status") == "FAILED" for r in report["drawings"]) else "COMPLETED"
    write_json(output / "batch-report.json", report)
    write_index(output, report)
    return report


def register(commands: argparse._SubParsersAction) -> None:
    batch = commands.add_parser("batch", help="Portable directory conversion and delivery contract")
    sub = batch.add_subparsers(dest="batch_command", required=True)
    init = sub.add_parser("prepare")
    init.add_argument("--inputs", type=Path, required=True)
    init.add_argument("--output", type=Path, required=True)
    init.set_defaults(handler=lambda a: (prepare(a.inputs, a.output), 0))
    run = sub.add_parser("run")
    run.add_argument("--contract", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--timeout", type=int, default=1800)
    run.set_defaults(handler=_run_command)
    pack = sub.add_parser("package")
    pack.add_argument("--run-dir", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.set_defaults(handler=_package_command)


def _run_command(args):
    result = run_batch(args.contract, args.output, timeout=args.timeout)
    return result, 2 if result["status"] == "FAILED" else 0


def _package_command(args):
    from .delivery import package_delivery
    return package_delivery(args.run_dir, args.output), 0
