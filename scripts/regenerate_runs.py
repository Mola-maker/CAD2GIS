#!/usr/bin/env python3
"""Regenerate the four baseline ``run/`` directories on the robustness branch.

Each run uses the existing reviewed project pack (``baselines/<site>/config``)
and the same supervision mode as the committed baselines: ``--llm assist``.
The script does NOT run onboarding or rewrite profiles/registries.

Fill ``API_KEY`` below with your DeepSeek API key, or export
``DEEPSEEK_API_KEY`` in the environment (environment wins).  The key is passed
to child processes only and is never printed or written to logs.

Examples:

    # edit API_KEY first, then:
    python scripts/regenerate_runs.py

    # preview commands without running
    python scripts/regenerate_runs.py --dry-run

    # regenerate one project
    python scripts/regenerate_runs.py --site hutabohu

    # use new-api provider instead of deepseek
    python scripts/regenerate_runs.py --provider new-api
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# TODO: fill in your DeepSeek API key, or leave empty and export
# DEEPSEEK_API_KEY before running.  For new-api, fill/export NEW_API_API_KEY.
API_KEY = ""
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "scripts" / "logs"

PROJECTS: tuple[dict[str, str], ...] = (
    {
        "site": "hutabohu",
        "dwg": "raw/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg",
        "project": "baselines/hutabohu",
        "run_dir": "baselines/hutabohu/run",
    },
    {
        "site": "kletek",
        "dwg": "raw/APD - KLETEK RW 05 SIDOARJO.dwg",
        "project": "baselines/kletek",
        "run_dir": "baselines/kletek/run",
    },
    {
        "site": "lamteh_main",
        "dwg": "raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg",
        "project": "baselines/lamteh_main",
        "run_dir": "baselines/lamteh_main/run",
    },
    {
        "site": "lamteh_sf",
        "dwg": "raw/APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg",
        "project": "baselines/lamteh_sf",
        "run_dir": "baselines/lamteh_sf/run",
    },
)


def _project_by_site(site: str) -> dict[str, str]:
    for project in PROJECTS:
        if project["site"] == site:
            return project
    raise KeyError(site)


def _command(project: dict[str, str], llm: str) -> list[str]:
    binary = shutil.which("cad2gis")
    prefix = [binary] if binary else [sys.executable, "-m", "cad2gis"]
    return [
        *prefix,
        "convert",
        str(ROOT / project["dwg"]),
        "--project",
        str(ROOT / project["project"]),
        "--run-dir",
        str(ROOT / project["run_dir"]),
        "--llm",
        llm,
        "--json",
    ]


def _environment(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    # The reviewed baselines are Model-space runs.  A stale CAD2GIS_LAYOUT
    # would silently target a named layout tab instead.
    if env.pop("CAD2GIS_LAYOUT", None):
        print("note: removed CAD2GIS_LAYOUT from the environment for this run")
    provider = args.provider
    if provider == "deepseek":
        key = env.get("DEEPSEEK_API_KEY", "").strip() or API_KEY.strip()
        if not args.dry_run and args.llm != "off" and not key:
            raise SystemExit(
                "DEEPSEEK_API_KEY is empty. Fill API_KEY inside "
                "scripts/regenerate_runs.py or export DEEPSEEK_API_KEY."
            )
        if key:
            env["DEEPSEEK_API_KEY"] = key
        env["CAD2GIS_LLM_PROVIDER"] = "deepseek"
    else:
        if (
            not args.dry_run
            and args.llm != "off"
            and not env.get("NEW_API_API_KEY", "").strip()
        ):
            raise SystemExit(
                "NEW_API_API_KEY is empty. Export it or pass --provider deepseek."
            )
        env["CAD2GIS_LLM_PROVIDER"] = "new_api"
    return env


def _run_one(
    project: dict[str, str],
    args: argparse.Namespace,
    env: dict[str, str],
) -> dict[str, Any]:
    command = _command(project, args.llm)
    site = project["site"]
    started = time.time()
    record: dict[str, Any] = {
        "site": site,
        "command": shlex.join(command),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "pending",
    }
    if args.dry_run:
        print(f"[dry-run] {record['command']}")
        record["status"] = "dry_run"
        return record

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    stdout_path = LOG_DIR / f"regenerate-{site}-{stamp}.out.log"
    stderr_path = LOG_DIR / f"regenerate-{site}-{stamp}.err.log"
    print(f"[{site}] regenerating {project['run_dir']} (llm={args.llm})")
    print(f"[{site}] logs: {stdout_path.name} / {stderr_path.name}")

    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
    elapsed = round(time.time() - started, 2)
    record["returncode"] = completed.returncode
    record["elapsed_s"] = elapsed
    record["stdout_log"] = str(stdout_path.relative_to(ROOT))
    record["stderr_log"] = str(stderr_path.relative_to(ROOT))
    record["status"] = "ok" if completed.returncode == 0 else "failed"
    record["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if completed.returncode != 0:
        tail = stderr_path.read_text(encoding="utf-8")[-2000:]
        print(f"[{site}] FAILED in {elapsed}s; stderr tail:")
        print(tail)
    else:
        print(f"[{site}] OK in {elapsed}s")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the four APD baseline run/ directories."
    )
    parser.add_argument(
        "--site",
        action="append",
        choices=[project["site"] for project in PROJECTS],
        help="Only regenerate this site; repeatable.",
    )
    parser.add_argument(
        "--llm",
        choices=("off", "observe", "assist"),
        default="assist",
        help="LLM supervisor mode (default: %(default)s, matching baselines).",
    )
    parser.add_argument(
        "--provider",
        choices=("deepseek", "new-api"),
        default="deepseek",
        help="LLM provider (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the commands that would be executed and exit.",
    )
    args = parser.parse_args(argv)

    sites = list(args.site or [project["site"] for project in PROJECTS])
    projects = [_project_by_site(site) for site in sites]
    missing = [
        project["site"]
        for project in projects
        if not (ROOT / project["dwg"]).is_file()
        or not (ROOT / project["project"]).is_dir()
        or not (ROOT / project["project"] / "config" / "source_profile.json").is_file()
        or not (ROOT / project["project"] / "config" / "mapping_registry.json").is_file()
    ]
    if missing:
        raise SystemExit(
            "Missing source DWG or reviewed project config for: " + ", ".join(missing)
        )

    env = _environment(args)
    records = [_run_one(project, args, env) for project in projects]
    failed = [record for record in records if record["status"] == "failed"]
    if not args.dry_run:
        summary_path = LOG_DIR / "regenerate-runs-summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "llm": args.llm,
                    "provider": args.provider,
                    "records": records,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"summary: {summary_path.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
