#!/usr/bin/env python3
"""Regenerate the four APD (As Plan Drawing) project packs with AI-supervised ``auto-convert``.

Unlike ``regenerate_runs.py`` (which replays existing reviewed JSON), this
script intentionally lets ``cad2gis auto-convert`` re-run the AI onboarding
pipeline and write the reviewed JSON files:

* ``baselines/<site>/config/source_profile.json``
* ``baselines/<site>/config/mapping_registry.json``
* ``baselines/<site>/config/spatial_regions.json`` (assist mode)
* ``baselines/<site>/review/ai_onboarding_result.json``

Use it after reader / plan-domain / semantics changes (issue 4) when the old
reviewed feature-count gates are no longer valid.

Safety: before each conversion the script archives
``baselines/<site>/config`` and ``baselines/<site>/review`` into
``scripts/logs/backups/``.  Run with ``--dry-run`` first.

Export ``DEEPSEEK_API_KEY`` in the environment before running.
For new-api, export the ``NEW_API_*`` variables and pass ``--provider new-api``.

Examples:

    python scripts/auto_convert_runs.py --dry-run
    python scripts/auto_convert_runs.py
    python scripts/auto_convert_runs.py --site kletek --force-bootstrap
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Set DEEPSEEK_API_KEY in the environment before running.  The key is passed
# to child processes only and is never printed or written to logs.
API_KEY = ""
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "scripts" / "logs"
BACKUP_DIR = LOG_DIR / "backups"

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
    {
        "site": "semarang_sf",
        "dwg": "raw/APD - BULU LOR RW 05 SEMARANG - SF.dwg",
        "project": "baselines/semarang_sf",
        "run_dir": "baselines/semarang_sf/run",
    },
    {
        "site": "darat_sekip_sf",
        "dwg": "raw/APD - DARAT SEKIP RW 12 PONTIANAK - SF.dwg",
        "project": "baselines/darat_sekip_sf",
        "run_dir": "baselines/darat_sekip_sf/run",
    },
    {
        "site": "manado-tomohon_uplink",
        "dwg": "raw/APD - MANADO- UPLINK_FWA_OLT_TOMOHON_TO_EMR- 46478_FO_24C.dwg",
        "project": "baselines/manado-tomohon_uplink",
        "run_dir": "baselines/manado-tomohon_uplink/run",
    },
    {
        "site": "tinggede",
        "dwg": "raw/APD - PERUMAHAN TINGGEDE VIEW PALU.dwg",
        "project": "baselines/tinggede",
        "run_dir": "baselines/tinggede/run",
    },
    {
        "site": "taipa",
        "dwg": "raw/APD - TAIPA RW 05 PALU.dwg",
        "project": "baselines/taipa",
        "run_dir": "baselines/taipa/run",
    },
    {
        "site": "tinggar",
        "dwg": "raw/APD - TINGGAR RW 04 SERANG.dwg",
        "project": "baselines/tinggar",
        "run_dir": "baselines/tinggar/run",
    },
)


def _project_by_site(site: str) -> dict[str, str]:
    for project in PROJECTS:
        if project["site"] == site:
            return project
    raise KeyError(site)


def _command(
    project: dict[str, str],
    *,
    provider: str,
    llm: str,
    force_bootstrap: bool,
) -> list[str]:
    binary = shutil.which("cad2gis")
    prefix = [binary] if binary else [sys.executable, "-m", "cad2gis"]
    command = [
        *prefix,
        "auto-convert",
        str(ROOT / project["dwg"]),
        "--project",
        str(ROOT / project["project"]),
        "--run-dir",
        str(ROOT / project["run_dir"]),
        "--provider",
        provider,
        "--llm",
        llm,
        "--json",
    ]
    if force_bootstrap:
        command.append("--force-bootstrap")
    return command


def _environment(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    if env.pop("CAD2GIS_LAYOUT", None):
        print("note: removed CAD2GIS_LAYOUT from the environment for this run")
    if args.provider == "deepseek":
        key = env.get("DEEPSEEK_API_KEY", "").strip() or API_KEY.strip()
        if not args.dry_run and not key:
            raise SystemExit(
                "DEEPSEEK_API_KEY is empty. Export DEEPSEEK_API_KEY before running."
            )
        if key:
            env["DEEPSEEK_API_KEY"] = key
        env["CAD2GIS_LLM_PROVIDER"] = "deepseek"
    else:
        if not args.dry_run and not env.get("NEW_API_API_KEY", "").strip():
            raise SystemExit(
                "NEW_API_API_KEY is empty. Export it or pass --provider deepseek."
            )
        env["CAD2GIS_LLM_PROVIDER"] = "new_api"
    return env


def _backup(project: dict[str, str], args: argparse.Namespace) -> str | None:
    project_dir = ROOT / project["project"]
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup_name = f"{project['site']}-{stamp}"
    backup_path = BACKUP_DIR / f"{backup_name}.tar.gz"
    if args.dry_run or args.no_backup:
        return str(backup_path) if args.dry_run else None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[Path] = []
    for name in ("config", "review"):
        path = project_dir / name
        if path.is_dir():
            entries.append(path)
    if not entries:
        print(f"[{project['site']}] no config/review directories to back up")
        return None
    with tarfile.open(backup_path, "w:gz") as archive:
        for path in entries:
            archive.add(path, arcname=f"{project['site']}/{path.name}")
    print(f"[{project['site']}] backup: {backup_path.relative_to(ROOT)}")
    return str(backup_path)


def _run_one(
    project: dict[str, str],
    args: argparse.Namespace,
    env: dict[str, str],
) -> dict[str, Any]:
    command = _command(
        project,
        provider=args.provider,
        llm=args.llm,
        force_bootstrap=args.force_bootstrap,
    )
    site = project["site"]
    record: dict[str, Any] = {
        "site": site,
        "command": shlex.join(command),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "pending",
        "backup": None,
    }
    if args.dry_run:
        print(f"[dry-run] {record['command']}")
        record["status"] = "dry_run"
        return record

    backup_path = _backup(project, args)
    record["backup"] = (
        str(Path(backup_path).relative_to(ROOT)) if backup_path else None
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    stdout_path = LOG_DIR / f"auto-convert-{site}-{stamp}.out.log"
    stderr_path = LOG_DIR / f"auto-convert-{site}-{stamp}.err.log"
    print(f"[{site}] auto-converting {project['project']} (provider={args.provider}, llm={args.llm})")
    print(f"[{site}] logs: {stdout_path.name} / {stderr_path.name}")

    started = time.time()
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
    record.update({
        "returncode": completed.returncode,
        "elapsed_s": elapsed,
        "stdout_log": str(stdout_path.relative_to(ROOT)),
        "stderr_log": str(stderr_path.relative_to(ROOT)),
        "status": "ok" if completed.returncode == 0 else "failed",
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    if completed.returncode != 0:
        print(f"[{site}] FAILED in {elapsed}s; stderr tail:")
        print(stderr_path.read_text(encoding="utf-8")[-2000:])
    else:
        print(f"[{site}] OK in {elapsed}s")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI-supervised auto-convert for the four APD (As Plan Drawing) projects."
    )
    parser.add_argument(
        "--site",
        action="append",
        choices=[project["site"] for project in PROJECTS],
        help="Only auto-convert this site; repeatable.",
    )
    parser.add_argument(
        "--provider",
        choices=("deepseek", "new-api"),
        default="deepseek",
        help="AI onboarding provider (default: %(default)s).",
    )
    parser.add_argument(
        "--llm",
        choices=("off", "observe", "assist"),
        default="assist",
        help="LLM supervisor mode for the conversion phase (default: %(default)s).",
    )
    parser.add_argument(
        "--force-bootstrap",
        action="store_true",
        help="Pass --force-bootstrap: replace managed project-pack files before AI onboarding.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip config/review backup (not recommended).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands and exit without running or backing up.",
    )
    args = parser.parse_args(argv)

    sites = list(args.site or [project["site"] for project in PROJECTS])
    projects = [_project_by_site(site) for site in sites]
    missing = [
        project["site"]
        for project in projects
        if not (ROOT / project["dwg"]).is_file()
        or not (ROOT / project["project"]).is_dir()
    ]
    if missing:
        raise SystemExit(
            "Missing source DWG or project directory for: " + ", ".join(missing)
        )

    env = _environment(args)
    records = [_run_one(project, args, env) for project in projects]
    failed = [record for record in records if record["status"] == "failed"]
    if not args.dry_run:
        summary_path = LOG_DIR / "auto-convert-runs-summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "provider": args.provider,
                    "llm": args.llm,
                    "force_bootstrap": args.force_bootstrap,
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
