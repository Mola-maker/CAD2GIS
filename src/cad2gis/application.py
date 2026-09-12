"""Authenticated application shell around the canonical CLI and review service.

The shell owns processes and file admission, not CAD conversion rules. Desktop
and remote browser clients use the same service; MCP remains a separate entry.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
import webbrowser


COOKIE = "cad2gis_session"
SESSION_SECONDS = 12 * 60 * 60
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
ASSETS = Path(__file__).resolve().parent / "desktop"
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
_DELIVERY_FILES = {
    "delivery-manifest.json": "QGIS 交付清单", "checksums.json": "文件校验清单", "README.md": "打开说明",
    "delivery.gpkg": "GIS 交付数据", "source.gpkg": "CAD 源事实数据", "evidence.gpkg": "来源证据数据",
    "run_manifest.json": "运行清单", "partition_manifest.json": "分区清单", "osm_matching.json": "OSM 匹配证据",
    "svg-candidates/index.html": "SVG / PNG 图例", "svg-candidates/correspondence.html": "SVG 来源对应报告",
    "svg-candidates/manifest.json": "SVG 图例清单", "svg-candidates/correspondence.json": "SVG 对应结构化报告",
    "svg-candidates/selection.json": "SVG 候选选择", "svg-candidates/symbols.sqlite3": "SVG 符号数据库",
    "qgis/styles/style_manifest.json": "QGIS 样式清单", "reasoning/visual/manifest.json": "源图视觉证据清单",
    "delivery.review.json": "QGIS 外部数据绑定核验", "osm-basemap.gpkg": "离线 OSM 底图",
    "view-with-source-SVG.review.json": "QGIS 独立图纸绑定核验",
}
# No recursive filesystem walk or manifest-controlled paths: only these product
# output directories and file types can appear in the download catalog.
_DELIVERY_SUFFIXES = {
    "": {".qgz", ".qgs", ".html", ".png", ".svg"},
    "qgis": {".qgz", ".qgs"}, "qgis/styles": {".qml"}, "styles": {".qml"},
    "svg-candidates": set(), "svg-candidates/symbols": {".svg", ".png"},
    "reasoning/visual": {".png", ".svg"},
    "audit": {".html", ".png", ".json"}, "visual": {".html", ".png", ".json"},
    "reports": {".html", ".png", ".json"},
}


def _host_name(value: str) -> str:
    parsed = urlsplit("//" + value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.path:
        raise ValueError("Invalid host")
    # Reading port also rejects invalid port strings.
    _ = parsed.port
    return parsed.hostname.lower().rstrip(".")


@dataclass
class ApplicationConfig:
    roots: tuple[Path, ...]
    hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    remote: bool = False
    password: str = field(default="", repr=False)
    secure: bool = False
    launch_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    state_dir: Path | None = None

    def __post_init__(self):
        self.roots = tuple(dict.fromkeys(Path(p).expanduser().resolve() for p in self.roots))
        if not self.roots or any(not p.is_dir() for p in self.roots):
            raise ValueError("At least one existing project root is required")
        self.hosts = tuple(h.lower().rstrip(".") for h in self.hosts)
        if not self.hosts or any(h in {"*", "0.0.0.0", "::"} for h in self.hosts):
            raise ValueError("Use explicit client-facing host names")
        if self.remote and (not self.secure or len(self.password) < 12):
            raise ValueError("Remote access requires TLS and CAD2GIS_APP_PASSWORD (at least 12 characters)")
        self.state_dir = (self.state_dir or self.roots[0] / ".cad2gis-app").expanduser().resolve()

    def path(self, value: Any, *, exists: bool = True, kind: str = "") -> Path:
        if not isinstance(value, str) or not value.strip() or len(value) > 32768:
            raise ValueError("请选择有效的服务器路径")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("请选择完整的服务器路径")
        path = path.resolve()
        if self.state_dir and (path == self.state_dir or self.state_dir in path.parents):
            raise ValueError("服务配置和凭证目录不属于项目文件")
        if not any(path == root or root in path.parents for root in self.roots):
            raise ValueError("路径超出了允许的项目目录")
        if exists and not path.exists():
            raise ValueError("路径不存在")
        if exists and kind == "directory" and not path.is_dir():
            raise ValueError("请选择目录")
        if exists and kind == "source" and (not path.is_file() or path.suffix.lower() != ".dwg"):
            raise ValueError("请选择 DWG 文件；DXF 请先在 CAD 软件中另存为 DWG")
        return path

    def admit_run(self, directory: Path, workspace: Path):
        """Check recorded file references before handing a run to the review API.

        The review application can replay its source/profile bindings. A run
        stored inside a root must not grant access to recorded paths outside it.
        Existing profiles remain trusted project configuration, not a sandbox
        for arbitrary Python or a replacement for canonical manifest validation.
        """
        path_keys = {"path", "run_dir", "workspace_dir", "source_run", "source_run_dir",
                     "store_path", "semantic_store", "manifest_path"}

        def check(value, base: Path, key=""):
            if isinstance(value, dict):
                for child_key, child in value.items():
                    check(child, base, child_key)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    check(child, base, "path" if key == "font_dirs" else key)
            elif key in path_keys and isinstance(value, str) and value.strip():
                path = Path(value).expanduser()
                # Legacy artifact/registration helpers resolve against cwd,
                # while source/replay helpers resolve against the run. Admit
                # both possible existing references before either can be read.
                # This also covers an ambiguous relative reference whose cwd
                # target exists outside the approved project roots.
                candidates = (path,) if path.is_absolute() else (path, base / path)
                # Copied runs retain old absolute provenance. The canonical
                # review helper may use a hash-bound local fallback when the
                # old file no longer exists; do not reject that portable case.
                for candidate in candidates:
                    if candidate.exists():
                        self.path(str(candidate.resolve()))

        manifests = [directory / "run_manifest.json", directory / "partition_manifest.json",
                     workspace / "matching-active.json"]
        if manifests[1].is_file():
            manifests.append(directory.parent / "run_manifest.json")
        for target in (workspace / "review.sqlite3", workspace / "review.sqlite3-wal",
                       workspace / "review.sqlite3-shm", workspace / "matching",
                       directory / "reasoning" / "visual", directory / "svg-candidates"):
            self.path(str(target), exists=False)
        seen = set()
        for manifest in manifests:
            if manifest in seen:
                continue
            seen.add(manifest)
            if manifest.is_file():
                self.path(str(manifest))
                if manifest.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError("运行清单过大")
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                check(payload, manifest.parent)
                if manifest.name in {"run_manifest.json", "partition_manifest.json"}:
                    # The canonical reader can reconnect a copied run through
                    # hash-bound portable files. Check these candidates before
                    # the reader follows a symlink/junction or hashes contents.
                    from cad2gis.review_server import _PORTABLE_ARTIFACT_PATHS

                    for relative in _PORTABLE_ARTIFACT_PATHS.values():
                        self.path(str(manifest.parent / relative), exists=False)
                    source = payload.get("source")
                    declared = source.get("path") if isinstance(source, dict) else None
                    if isinstance(declared, str) and declared.strip():
                        name = Path(declared).expanduser().name
                        for candidate in (manifest.parent / name, manifest.parent / "source" / name):
                            self.path(str(candidate), exists=False)
                if manifest.name == "matching-active.json":
                    for key in ("source_run_dir", "run_dir"):
                        if isinstance(payload.get(key), str):
                            active_run = self.path(payload[key], kind="directory")
                            manifests.append(active_run / "run_manifest.json")
                            manifests.append(active_run.parent / "run_manifest.json")


@dataclass
class Session:
    expires: float
    review: Any = None
    run_dir: str = ""
    review_id: str = ""
    sockets: list[Any] = field(default_factory=list)


class Jobs:
    """One owned CLI process at a time; bounded logs and explicit cancellation."""

    def __init__(self, config: ApplicationConfig, settings=None):
        self.config = config
        self.settings = settings
        self.records: dict[str, dict[str, Any]] = {}
        self.process: subprocess.Popen | None = None
        self.lock = threading.RLock()
        self.active = ""
        self.closed = False

    def command(self, payload: dict[str, Any]) -> tuple[list[str], str]:
        action = payload.get("action")
        if action == "doctor":
            return ["doctor", "--deep", "--strict", "--profile", "full", "--json"], ""
        if action == "semantic-extract":
            source = self.config.path(payload.get("source"), kind="source")
            output = self.config.path(payload.get("run"), exists=False)
            if output.exists():
                raise ValueError("结果目录已存在，请使用新的目录以保留历史结果")
            return ["export-source", str(source), "--run-dir", str(output / "source"), "--json"], str(output)
        if action not in {"inspect", "bootstrap", "convert", "auto-convert"}:
            raise ValueError("不支持的操作")
        source = self.config.path(payload.get("source"), kind="source")
        args = [action, str(source)]
        run = ""
        if action != "inspect":
            project = self.config.path(payload.get("project"), exists=action == "convert", kind="directory")
            args += ["--project", str(project)]
        if action in {"convert", "auto-convert"}:
            run_path = self.config.path(payload.get("run"), exists=False)
            if run_path.exists():
                raise ValueError("结果目录已存在，请使用新的目录以保留历史结果")
            run = str(run_path)
            args += ["--run-dir", run, "--matching", "osm", "--svg-mode", "candidate"]
            place = payload.get("osm_place", "")
            if not isinstance(place, str) or len(place) > 500:
                raise ValueError("OSM 地点名称过长或无效")
            if place.strip():
                args += ["--osm-place", place.strip()]
        if action == "auto-convert":
            provider = payload.get("provider", "deepseek")
            if provider not in {"deepseek", "new-api"}:
                raise ValueError("不支持的 AI 服务")
            args += ["--provider", provider]
        return args + ["--json"], run

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        args, run = self.command(payload)
        if self.settings and payload.get("action") == "auto-convert":
            self.settings.require(payload.get("provider"))
        with self.lock:
            if self.closed or self.active:
                raise RuntimeError("已有任务正在执行，请等待完成或先取消任务")
            job_id = secrets.token_hex(10)
            record = {"id": job_id, "action": payload["action"], "status": "queued", "log": "",
                       "run_dir": run, "exit_code": None, "created": time.time(),
                       "stage": "queued", "completed_stages": 0,
                       "total_stages": 3 if payload["action"] == "semantic-extract" else 1,
                       "artifacts": []}
            self.records[job_id] = record
            self.active = job_id
            # Old logs are not a durable run record; manifests remain authoritative.
            while len(self.records) > 30:
                self.records.pop(next(iter(self.records)))
            threading.Thread(target=self._run, args=(job_id, args), daemon=True).start()
            return dict(record)

    def _run(self, job_id: str, args: list[str]):
        record = self.records[job_id]
        environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
                           CAD2GIS_PROJECT_ROOTS=os.pathsep.join(map(str, self.config.roots)))
        if self.settings:
            provider = args[args.index("--provider") + 1] if "--provider" in args else None
            environment.update(self.settings.environment(provider))
        hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            semantic = record["action"] == "semantic-extract"
            stages = [("source" if semantic else "execute", args)]
            if semantic:
                output = self.config.path(record["run_dir"], exists=False)
                # Reserve a fresh directory atomically. Never reuse an accepted run.
                with self.lock:
                    if record["status"] in {"cancelling", "cancelled"} or self.closed:
                        record["status"] = "cancelled"
                        return
                    output.mkdir(parents=True, exist_ok=False)
                stages.append(("candidates", ["semantic", "prepare", "--source-run", str(output / "source"),
                                               "--output-dir", str(output / "candidates"), "--json"]))
            for stage, command in stages:
                with self.lock:
                    if record["status"] in {"cancelling", "cancelled"} or self.closed:
                        record["status"] = "cancelled"
                        return
                    record.update(status="running", stage=stage)
                    if semantic:
                        record["log"] += "\n" + {"source": "读取原始 CAD 事实…", "candidates": "建立来源绑定的语义候选…"}[stage] + "\n"
                    self.process = subprocess.Popen(
                        [sys.executable, "-I", "-B", "-u", "-X", "utf8", "-m", "cad2gis.cli", *command], cwd=str(self.config.roots[0]),
                        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **hidden,
                    )
                    process = self.process
                assert process.stdout is not None
                for line in process.stdout:
                    for name in ("CAD2GIS_APP_PASSWORD", "DEEPSEEK_API_KEY", "NEWAPI_API_KEY", "NEW_API_API_KEY"):
                        secret = environment.get(name, "")
                        if secret:
                            line = line.replace(secret, "[已隐藏]")
                    with self.lock:
                        record["log"] = (record["log"] + line)[-131072:]
                code = process.wait()
                with self.lock:
                    self.process = None
                    record["exit_code"] = code
                    if record["status"] in {"cancelling", "cancelled"}:
                        record["status"] = "cancelled"
                        return
                    if code != 0:
                        record["status"] = "failed"
                        return
                    record["completed_stages"] += 1
            if semantic:
                with self.lock:
                    if record["status"] in {"cancelling", "cancelled"}:
                        record["status"] = "cancelled"
                        return
                    record["stage"] = "verify"
                # Use canonical validators, not a zero exit code or a file count,
                # to establish that the source and candidate manifests agree.
                artifacts = self.semantic_artifacts(record["run_dir"])
                with self.lock:
                    if record["status"] in {"cancelling", "cancelled"}:
                        record["status"] = "cancelled"
                        return
                    record.update(artifacts=artifacts["artifacts"], summary=artifacts["summary"], completed_stages=3)
            with self.lock:
                if record["status"] not in {"cancelling", "cancelled"}:
                    record.update(status="succeeded", stage="complete")
        except Exception as exc:
            with self.lock:
                if record["status"] not in {"cancelling", "cancelled"}:
                    record.update(status="failed", log=(record["log"] + f"\n无法运行任务：{exc}")[-131072:])
        finally:
            with self.lock:
                if record["status"] == "cancelling":
                    record["status"] = "cancelled"
                self.process = None
                self.active = ""

    def semantic_artifacts(self, run: str) -> dict[str, Any]:
        """Admit fixed output paths before canonical source/candidate validation."""
        root = self.config.path(run, kind="directory")
        source = self.config.path(str(root / "source"), kind="directory")
        source_manifest = self.config.path(str(source / "source_manifest.json"))
        prepare_manifest = self.config.path(str(root / "candidates/manifest.json"))
        if max(source_manifest.stat().st_size, prepare_manifest.stat().st_size) > 8 * 1024 * 1024:
            raise ValueError("语义提取清单过大")
        facts = json.loads(source_manifest.read_text(encoding="utf-8"))
        if not isinstance(facts, dict) or not isinstance(facts.get("artifacts"), dict):
            raise ValueError("语义源清单结构无效")
        for artifact in facts.get("artifacts", {}).values():
            if not isinstance(artifact, dict):
                raise ValueError("语义源产物记录无效")
            self.config.path(artifact.get("path"))
        paths = [("source_manifest", "源事实清单", source_manifest, "json"),
                 ("prepare_manifest", "语义候选清单", prepare_manifest, "json"),
                 ("source_database", "源事实数据库", source / "source.gpkg", "database"),
                 ("candidate_database", "语义候选数据库", root / "candidates/candidate_index.sqlite3", "database")]
        for _, _, path, _ in paths:
            self.config.path(str(path))
        from .cad2gis_v3.semantic_stage import _bound_context

        manifest, _, _ = _bound_context(source, prepare_manifest)
        return {"run_dir": str(root), "summary": {
                    "entity_count": manifest["entity_count"], "candidate_count": manifest["candidate_count"],
                    "source_sha256": manifest["source_sha256"], "snapshot_sha256": manifest["snapshot_sha256"],
                    "status": manifest["status"], "semantic_committed": False,
                }, "artifacts": [{"name": name, "label": label, "path": str(path), "kind": kind}
                                 for name, label, path, kind in paths]}

    def _delivery_root(self, run: str) -> Path:
        root = self.config.path(run, kind="directory")
        supplied = Path(run).expanduser().absolute()
        for path in (supplied, *supplied.parents):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("交付目录不能通过符号链接打开")
        return root

    def _delivery_path(self, root: Path, name: str) -> Path:
        if (not isinstance(name, str) or not name or len(name) > 512 or "\\" in name
                or ":" in name or any(part in {"", ".", ".."} or part.startswith(".")
                                      for part in name.split("/"))):
            raise ValueError("交付文件名无效")
        relative = PurePosixPath(name)
        parent = "" if str(relative.parent) == "." else str(relative.parent)
        if name not in _DELIVERY_FILES and relative.suffix.lower() not in _DELIVERY_SUFFIXES.get(parent, set()):
            raise ValueError("交付文件不在允许清单中")
        target = root
        for part in relative.parts:
            target /= part
            if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
                raise ValueError("交付文件不能使用符号链接")
        approved = self.config.path(str(target))
        if not approved.is_relative_to(root) or not approved.is_file():
            raise ValueError("交付文件不存在")
        return approved

    def delivery_artifacts(self, run: str) -> dict[str, Any]:
        """List existing local outputs without changing or upgrading run status."""
        root = self._delivery_root(run)
        if (root / "delivery.review.json").exists() or (root / "view-with-source-SVG.review.json").exists():
            record = self.standard_delivery_scope(str(root))
            from .match_delivery import DELIVERY_FILES

            return {**record, "artifacts": [
                {"name": name, "label": _DELIVERY_FILES.get(name, name),
                 "kind": Path(name).suffix.lstrip("."), "size": self._delivery_path(root, name).stat().st_size,
                 "url": "/_app/api/delivery/artifact?" + urlencode({"run": str(root), "name": name})}
                for name in DELIVERY_FILES]}
        manifest_name = next((name for name in ("run_manifest.json", "partition_manifest.json", "delivery-manifest.json")
                              if (root / name).exists()), "run_manifest.json")
        manifest = self._delivery_path(root, manifest_name)
        if manifest.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("交付运行清单过大")
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("交付运行清单无效") from exc
        if not isinstance(record, dict):
            raise ValueError("交付运行清单无效")
        candidates = set(_DELIVERY_FILES)
        for relative in _DELIVERY_SUFFIXES:
            folder = root
            for part in PurePosixPath(relative).parts:
                folder /= part
                if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
                    break
            else:
                if folder.is_dir():
                    for entry in folder.iterdir():
                        candidates.add(entry.relative_to(root).as_posix())
                        if len(candidates) > 20000:
                            raise ValueError("交付文件过多，请选择单个图页的结果目录")
        artifacts = []
        for name in sorted(candidates, key=lambda name: (name not in _DELIVERY_FILES, name)):
            try:
                path = self._delivery_path(root, name)
            except ValueError:
                continue  # Unrelated files and links are never advertised.
            kind = path.suffix.lower().lstrip(".")
            artifacts.append({"name": name, "label": _DELIVERY_FILES.get(name, path.name), "kind": kind,
                              "size": path.stat().st_size,
                              "url": "/_app/api/delivery/artifact?" + urlencode({"run": str(root), "name": name})})
        result = {"run_dir": str(root), "run_status": record.get("run_status", "UNKNOWN"), "artifacts": artifacts}
        if manifest_name == "delivery-manifest.json":
            archive = self.delivery_archive(str(root))
            result["archive"] = {"name": archive.name, "size": archive.stat().st_size,
                                 "url": "/_app/api/delivery/archive?" + urlencode({"run": str(root)})}
        return result

    def standard_delivery_scope(self, directory: str) -> dict[str, Any]:
        """Admit six-file scopes from their actual QGZ/database bindings, read-only."""
        from .match_delivery import DELIVERY_FILES
        from .qgis_review import OSM_URL, _matching_context

        root = self._delivery_root(directory)
        files = {name: self._delivery_path(root, name) for name in DELIVERY_FILES}

        def digest(path):
            with path.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()

        cad_sha, osm_sha = digest(files["delivery.gpkg"]), digest(files["osm-basemap.gpkg"])
        contexts, snapshots = [], []
        try:
            for project_name, standalone in (("delivery.qgz", False), ("view-with-source-SVG.qgz", True)):
                receipt_path = files[Path(project_name).with_suffix(".review.json").name]
                if receipt_path.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError("交付核验记录过大")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (not isinstance(receipt, dict) or receipt.get("schema") != "cad2gis.qgis-osm-review.v1"
                        or receipt.get("sha256") != digest(files[project_name])
                        or receipt.get("standalone") is not standalone
                        or receipt.get("cad_database_hashes") != [cad_sha]
                        or receipt.get("geometry_and_attributes_unchanged") is not True
                        or receipt.get("absolute_accuracy_verified") is not False):
                    raise ValueError("QGIS 交付核验记录与实际文件不匹配")
                contexts.append(_matching_context(receipt.get("canonical_matching"), [cad_sha]))
                snapshot = receipt.get("osm_source_sha256")
                if not isinstance(snapshot, str) or len(snapshot) != 64:
                    raise ValueError("QGIS 交付缺少冻结 OSM 来源绑定")
                snapshots.append(snapshot)
                with zipfile.ZipFile(files[project_name]) as bundle:
                    names = bundle.namelist()
                    documents = [name for name in names if name.endswith(".qgs")]
                    if (len(set(names)) != len(names) or len(documents) != 1
                            or any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                                   or "\\" in name or ":" in name for name in names)
                            or bundle.getinfo(documents[0]).file_size > 64 * 1024 * 1024):
                        raise ValueError("QGZ 项目结构无效")
                    document = ET.fromstring(bundle.read(documents[0]))
                    observed = {}
                    for layer in document.findall("projectlayers/maplayer"):
                        provider, source = layer.findtext("provider"), layer.findtext("datasource") or ""
                        if provider == "wms" and unquote(source) == "type=xyz&url=" + OSM_URL + "&zmin=0&zmax=19":
                            continue
                        if provider != "ogr" or "|layername=" not in source:
                            raise ValueError("QGIS 交付包含未绑定的数据源")
                        database = source.split("|", 1)[0]
                        if database in observed:
                            continue
                        if standalone:
                            attachment = database.removeprefix("attachment:")
                            if not database.startswith("attachment:") or attachment not in names or not attachment.endswith(".gpkg"):
                                raise ValueError("独立 QGZ 缺少绑定的数据库附件")
                            with bundle.open(attachment) as stream:
                                observed[database] = hashlib.file_digest(stream, "sha256").hexdigest()
                        else:
                            if database not in {"./delivery.gpkg", "./osm-basemap.gpkg"}:
                                raise ValueError("QGIS 交付引用了标准目录外的数据")
                            observed[database] = cad_sha if database == "./delivery.gpkg" else osm_sha
                    expected = {cad_sha, receipt.get("osm_database_sha256")}
                    if len(observed) != 2 or set(observed.values()) != expected:
                        raise ValueError("QGIS 内嵌或外部数据库哈希不匹配")
            if contexts[0] != contexts[1] or snapshots[0] != snapshots[1]:
                raise ValueError("两份 QGIS 图纸的匹配或冻结 OSM 来源不一致")
        except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError, KeyError, TypeError) as exc:
            raise ValueError("标准 QGIS 交付绑定无效") from exc
        context = contexts[0] or {}
        matching = context.get("matching", {})
        return {"run_dir": str(root), "run_status": "UNKNOWN", "delivery_sha256": cad_sha,
                "delivery_kind": "standard_matched" if matching.get("status") == "matched" else "standard_reference",
                "source_sha256": context.get("source_sha256"), "run_manifest_sha256": context.get("run_manifest_sha256"),
                "matching": {key: matching.get(key) for key in ("status", "revision", "match_sha256", "authority")},
                "absolute_accuracy_verified": False}

    def delivery_archive(self, run: str) -> Path:
        """Serve the complete, hash-bound sibling ZIP of a portable package."""
        root = self._delivery_root(run)
        manifest = self._delivery_path(root, "delivery-manifest.json")
        checksum_file = self._delivery_path(root, "checksums.json")
        archive = root.with_suffix(".zip")
        if archive.is_symlink() or getattr(archive, "is_junction", lambda: False)():
            raise ValueError("交付压缩包不能使用符号链接")
        archive = self.config.path(str(archive))
        if max(manifest.stat().st_size, checksum_file.stat().st_size) > 8 * 1024 * 1024:
            raise ValueError("交付清单过大")
        try:
            report = json.loads(manifest.read_text(encoding="utf-8"))
            checksums = json.loads(checksum_file.read_text(encoding="utf-8"))
            if (not isinstance(report, dict) or report.get("schema_version") != "cad2gis.delivery-package.v1"
                    or not isinstance(checksums, dict) or not checksums or "checksums.json" in checksums):
                raise ValueError("交付清单或文件校验清单无效")
            allowed = {"delivery-manifest.json", "checksums.json", "README.md"}
            bound_hashes = {}
            descriptors = report.get("deliveries")
            if not isinstance(descriptors, list) or not descriptors:
                raise ValueError("交付清单缺少图页")
            for entry in descriptors:
                project = PurePosixPath(entry["project"])
                if project.name != "delivery.qgz":
                    raise ValueError("交付项目路径无效")
                allowed.update(str(project.parent / name) for name in ("delivery.qgz", "delivery.gpkg"))
                bound_hashes[str(project.parent / "delivery.gpkg")] = entry["gpkg_sha256"]
                for layer in entry["layers"]:
                    name = layer["layer"]
                    if (not isinstance(name, str) or not name or any(char in name for char in "/\\:")
                            or name.startswith(".")):
                        raise ValueError("交付图层名无效")
                    allowed.add(str(project.parent / (name + ".csv")))
                    allowed.add(str(project.parent / "styles" / (name + ".qml")))
            if report.get("overview_project"):
                if report["overview_project"] != "all-districts.qgz":
                    raise ValueError("交付总图路径无效")
                allowed.add("all-districts.qgz")
            if report.get("geometry_repairs") == "candidate-only":
                allowed.add("geometry_repair_candidates.json")
            svg = report.get("svg_candidates") or {}
            allowed.update("svg-candidates/" + name for name in svg.get("files", {}))
            bound_hashes.update(("svg-candidates/" + name, sha) for name, sha in svg.get("files", {}).items())
            actual = {}
            pending = [root]
            while pending:
                for path in pending.pop().iterdir():
                    if (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                            or self.config.path(str(path)) != path):
                        raise ValueError("交付压缩目录不能包含符号链接")
                    if path.is_dir():
                        pending.append(path)
                        continue
                    name = path.relative_to(root).as_posix()
                    visual = (report.get("visual_audit_included") is True and name.startswith("visual/")
                              and path.suffix.lower() in {".json", ".html", ".png", ".svg", ".csv"})
                    if (not path.is_file() or any(part in {"", ".", ".."} or part.startswith(".")
                                                 for part in name.split("/"))
                            or ":" in name or "\\" in name or (name not in allowed and not visual)):
                        raise ValueError("交付压缩目录包含未允许的文件")
                    with path.open("rb") as stream:
                        actual[name] = hashlib.file_digest(stream, "sha256").hexdigest()
                    if len(actual) > 20000:
                        raise ValueError("交付压缩目录文件过多")
            if (not allowed <= set(actual) or set(actual) != {*checksums, "checksums.json"}
                    or any(actual[name] != sha for name, sha in checksums.items())
                    or any(actual.get(name) != sha for name, sha in bound_hashes.items())):
                raise ValueError("交付目录与文件校验清单不匹配")
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                if len(names) != len(actual) or set(names) != set(actual):
                    raise ValueError("ZIP 成员与当前交付目录不匹配，请重新打包")
                for name, expected in actual.items():
                    info = bundle.getinfo(name)
                    if info.file_size != (root / name).stat().st_size or (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("ZIP 文件大小或类型无效")
                    with bundle.open(info) as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                            raise ValueError("ZIP 文件哈希不匹配，请重新打包")
        except (OSError, KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            raise ValueError("交付 ZIP 缺失或无效，请重新打包") from exc
        return archive

    def list(self):
        with self.lock:
            return [dict(record) for record in reversed(list(self.records.values()))]

    def cancel(self, job_id: str):
        with self.lock:
            if job_id != self.active:
                raise ValueError("任务已结束或不存在")
            if self.records[job_id]["status"] in {"succeeded", "failed", "cancelled"}:
                raise ValueError("任务已结束或不存在")
            self.records[job_id]["status"] = "cancelling"
            process = self.process
        if process is not None and process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()

    def close(self):
        with self.lock:
            self.closed = True
            active = self.active
        if active:
            try:
                self.cancel(active)
            except ValueError:
                pass


class Application:
    def __init__(self, config: ApplicationConfig, *, review_factory=None):
        from fastapi import FastAPI, Request
        from fastapi.responses import FileResponse, JSONResponse
        from starlette.concurrency import run_in_threadpool

        self.config = config
        from .desktop_agent import DesktopAgent
        from .desktop_settings import AISettings

        self.settings = AISettings(config.state_dir)
        # Cookies are scoped to a host, not a TCP port. Separate local launches
        # must not overwrite each other's login when random ports are used.
        self.cookie_name = f"{COOKIE}_{secrets.token_hex(6)}"
        self.sessions: dict[str, Session] = {}
        self.jobs = Jobs(config, self.settings)
        self.agent = DesktopAgent(config, self.settings, self.jobs)
        self.ready = threading.Event()
        self.shutdown_callback = lambda: None
        self.review_factory = review_factory
        self.review_cache: dict[str, Any] = {}
        self.review_lock = asyncio.Lock()
        self.login_attempts: dict[str, list[float]] = {}

        @asynccontextmanager
        async def lifespan(_app):
            self.ready.set()
            try:
                yield
            finally:
                await self.agent.close()
                await run_in_threadpool(self.jobs.close)

        manager = FastAPI(title="CAD2GIS Application", docs_url=None, redoc_url=None,
                          openapi_url=None, lifespan=lifespan)
        self.manager = manager

        @manager.exception_handler(ValueError)
        async def invalid(_request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=400)

        @manager.exception_handler(RuntimeError)
        async def busy(_request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=409)

        @manager.get("/_app/")
        async def home():
            return FileResponse(ASSETS / "index.html")

        @manager.get("/_app/assets/{name:path}")
        async def asset(name: str):
            target = (ASSETS / name).resolve()
            if (not target.is_relative_to(ASSETS.resolve()) or not target.is_file()
                    or target.suffix.lower() not in {".js", ".css", ".png", ".webp", ".svg", ".ico", ".woff2"}):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return FileResponse(target)

        @manager.post("/_app/api/login")
        async def login(request: Request):
            key = request.client.host if request.client else "unknown"
            now = time.monotonic()
            attempts = [stamp for stamp in self.login_attempts.get(key, []) if now - stamp < 60]
            if len(attempts) >= 10:
                return JSONResponse({"detail": "尝试过于频繁，请一分钟后重试"}, status_code=429)
            body = await self.body(request)
            supplied = body.get("password" if config.remote else "token", "")
            expected = config.password if config.remote else config.launch_token
            if (not isinstance(supplied, str) or not expected
                    or not secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))):
                self.login_attempts[key] = [*attempts, now]
                return JSONResponse({"detail": "密码或启动链接无效"}, status_code=401)
            self.login_attempts.pop(key, None)
            if not config.remote:
                config.launch_token = ""  # One use; an intercepted old link cannot sign in again.
            token = secrets.token_urlsafe(32)
            self.sessions = {key: value for key, value in self.sessions.items() if value.expires > now}
            self.sessions[token] = Session(now + SESSION_SECONDS)
            response = JSONResponse({"status": "ok"})
            response.set_cookie(self.cookie_name, token, max_age=SESSION_SECONDS, httponly=True,
                                secure=config.secure, samesite="strict", path="/")
            return response

        @manager.post("/_app/api/logout")
        async def logout(request: Request):
            if not config.remote:
                return JSONResponse({"detail": "本地版请使用“关闭程序”结束会话"}, status_code=403)
            session = self.sessions.pop(request.scope["cad2gis_token"], None)
            if session:
                for close in list(session.sockets):
                    await close()
            response = JSONResponse({"status": "ok"})
            response.delete_cookie(self.cookie_name, path="/", secure=config.secure, httponly=True, samesite="strict")
            return response

        @manager.get("/_app/api/health")
        @manager.get("/_app/api/status")
        async def status(request: Request):
            session = self.sessions[request.scope["cad2gis_token"]]
            return {"status": "ok", "schema_version": "cad2gis.application.v1", "remote": config.remote,
                    "roots": list(map(str, config.roots)), "run_dir": session.run_dir,
                    "active_job": self.jobs.active, "matching": "osm", "svg_mode": "candidate",
                    "semantic_extraction": {"action": "semantic-extract", "available": True,
                                            "stages": ["export-source", "semantic prepare", "verify"],
                                            "automatic_commit": False},
                    "active_agent": self.agent.active,
                    "ai_provider": self.settings.public()["active"],
                    "provider_configured": {key: value["configured"] for key, value in self.settings.public()["profiles"].items()}}

        @manager.get("/_app/api/ai/config")
        async def ai_config():
            return {**self.settings.public(), "editable": not config.remote}

        @manager.post("/_app/api/ai/config")
        async def save_ai_config(request: Request):
            if config.remote:
                return JSONResponse({"detail": "请在服务器本机配置 AI 服务"}, status_code=403)
            if self.agent.active or self.jobs.active:
                raise RuntimeError("请等待当前任务完成后再修改 AI 配置")
            return await run_in_threadpool(self.settings.save, await self.body(request))

        @manager.post("/_app/api/ai/test")
        async def test_ai_config(request: Request):
            from .desktop_agent import test_connection
            body = await self.body(request)
            return await test_connection(self.settings, body.get("provider"))

        @manager.get("/_app/api/conversations")
        async def conversations(page: int = 1, page_size: int = 12):
            return self.agent.store.listing(page, page_size)

        @manager.post("/_app/api/conversations")
        async def new_conversation(request: Request):
            body = await self.body(request)
            title = body.get("title", "新的转换对话")
            if not isinstance(title, str):
                raise ValueError("对话标题无效")
            record = self.agent.store.create(title)
            return {"id": record["id"], "title": record["title"]}

        @manager.get("/_app/api/conversations/{identifier}")
        async def conversation_messages(identifier: str, before: int = 0):
            return self.agent.store.messages(identifier, before)

        @manager.post("/_app/api/agent")
        async def agent_message(request: Request):
            return self.agent.start(await self.body(request))

        @manager.get("/_app/api/agent/{identifier}")
        async def agent_status(identifier: str):
            return self.agent.store.run(identifier)

        @manager.post("/_app/api/agent/{identifier}/cancel")
        async def agent_cancel(identifier: str):
            await self.agent.cancel(identifier)
            return {"status": "cancelled"}

        @manager.get("/_app/api/database/schema")
        async def database_schema(path: str):
            from .desktop_database import schema
            return await run_in_threadpool(schema, config, path)

        @manager.get("/_app/api/semantic/artifacts")
        async def semantic_artifacts(run: str):
            return await run_in_threadpool(self.jobs.semantic_artifacts, run)

        @manager.get("/_app/api/semantic/artifact")
        async def semantic_artifact(run: str, name: str):
            result = await run_in_threadpool(self.jobs.semantic_artifacts, run)
            artifact = next((item for item in result["artifacts"] if item["name"] == name), None)
            if artifact is None:
                raise ValueError("语义产物不存在")
            return FileResponse(artifact["path"], filename=Path(artifact["path"]).name)

        @manager.get("/_app/api/delivery/artifacts")
        async def delivery_artifacts(run: str):
            return await run_in_threadpool(self.jobs.delivery_artifacts, run)

        @manager.get("/_app/api/delivery/artifact")
        async def delivery_artifact(run: str, name: str):
            result = await run_in_threadpool(self.jobs.delivery_artifacts, run)
            if not any(item["name"] == name for item in result["artifacts"]):
                raise ValueError("交付文件不存在或不在允许清单中")
            target = self.jobs._delivery_path(self.jobs._delivery_root(run), name)
            return FileResponse(target, filename=target.name)

        @manager.get("/_app/api/delivery/archive")
        async def delivery_archive(run: str):
            target = await run_in_threadpool(self.jobs.delivery_archive, run)
            return FileResponse(target, filename=target.name, media_type="application/zip")

        @manager.post("/_app/api/database/query")
        async def database_query(request: Request):
            from .desktop_database import query
            body = await self.body(request)
            page, size = body.get("page", 1), body.get("page_size", 50)
            if type(page) is not int or type(size) is not int:
                raise ValueError("分页参数无效")
            return await run_in_threadpool(query, config, body.get("path", ""), table=body.get("table", ""),
                                          sql=body.get("sql", ""), page=page, page_size=size)

        @manager.get("/_app/api/docs/{name}")
        async def document(name: str):
            if name not in {"guide", "architecture", "ai", "sources"}:
                raise ValueError("说明文档不存在")
            target = ASSETS / "docs" / f"{name}.md"
            return {"name": name, "content": target.read_text(encoding="utf-8")}

        @manager.post("/_app/api/upload")
        async def upload(request: Request, name: str, root: int = 0):
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/octet-stream":
                raise ValueError("Upload must use application/octet-stream")
            if (not 0 <= root < len(config.roots) or not name or len(name) > 160
                    or Path(name).name != name or PureWindowsPath(name).name != name
                    or any(char in name for char in '/\\:\x00<>|?*') or name.endswith((" ", "."))
                    or any(ord(char) < 32 for char in name)
                    or PureWindowsPath(name).is_reserved()
                    or Path(name).suffix.lower() != ".dwg"):
                raise ValueError("请选择名称有效的 DWG 文件；DXF 请先在 CAD 软件中另存为 DWG")
            length = request.headers.get("content-length", "")
            if length and (not length.isdigit() or int(length) > MAX_UPLOAD_BYTES):
                return JSONResponse({"detail": "图纸不得超过 512 MB"}, status_code=413)
            imports = config.path(str(config.roots[root] / "imports"), exists=False)
            imports.mkdir(exist_ok=True)
            destination = config.path(str(imports / secrets.token_hex(12)), exists=False)
            destination.mkdir(exist_ok=False)
            pending = destination / ".uploading"
            target = destination / name
            size = 0
            try:
                with pending.open("xb") as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise ValueError("图纸不得超过 512 MB")
                        output.write(chunk)
                if not size:
                    raise ValueError("图纸文件为空")
                pending.replace(target)
            except BaseException:
                pending.unlink(missing_ok=True)
                destination.rmdir()
                raise
            return {"source": str(target), "directory": str(destination), "size": size,
                    "project": str(destination / "project"), "run": str(destination / "run-1")}

        @manager.get("/_app/api/files")
        async def files(path: str = "", page: int = 1, page_size: int = 50, search: str = ""):
            from .desktop_database import page_bounds
            offset, limit = page_bounds(page, page_size)
            directory = config.path(path, kind="directory") if path else config.roots[0]
            entries = []
            for child in directory.iterdir():
                try:
                    if child.name.startswith(".") or search.casefold() not in child.name.casefold():
                        continue
                    resolved = config.path(str(child))
                    is_dir = resolved.is_dir()
                    if is_dir or resolved.suffix.lower() in {".dwg", ".json", ".db", ".sqlite", ".sqlite3", ".gpkg"}:
                        entries.append({"name": child.name, "path": str(resolved), "directory": is_dir})
                except (ValueError, OSError):
                    continue
            parent = None if directory in config.roots else str(directory.parent)
            entries.sort(key=lambda item: (not item["directory"], item["name"].casefold()))
            return {"path": str(directory), "parent": parent, "page": page, "total": len(entries),
                    "has_more": offset + limit < len(entries), "entries": entries[offset:offset + limit]}

        @manager.get("/_app/api/jobs")
        async def jobs():
            return {"jobs": self.jobs.list()}

        @manager.post("/_app/api/jobs")
        async def start_job(request: Request):
            if self.agent.active:
                raise RuntimeError("智能体正在执行任务，请等待完成或先停止")
            return self.jobs.start(await self.body(request))

        @manager.post("/_app/api/jobs/{job_id}/cancel")
        async def cancel_job(job_id: str):
            await run_in_threadpool(self.jobs.cancel, job_id)
            return {"status": self.jobs.records[job_id]["status"]}

        @manager.post("/_app/api/review")
        async def open_review(request: Request):
            body = await self.body(request)
            directory = config.path(body.get("run"), kind="directory")
            # Use the original review workspace convention without modifying
            # immutable run files. A root consisting only of a run is too narrow.
            workspace = config.path(str(directory.parent / f"{directory.name}.review"), exists=False)
            config.admit_run(directory, workspace)
            from .delivery_scopes import delivery_scopes

            district_choices = await run_in_threadpool(delivery_scopes, directory)
            for district in district_choices["scopes"]:
                config.path(district["run_dir"], exists=False)
            async with self.review_lock:
                key = str(directory)
                if key not in self.review_cache:
                    if len(self.review_cache) >= 16:
                        raise RuntimeError("已打开 16 个审查结果，请重启服务后继续")
                    factory = self.review_factory
                    if factory is None:
                        from .review_server import create_review_app
                        factory = create_review_app
                    self.review_cache[key] = await run_in_threadpool(factory, directory, workspace_dir=workspace)
                session = self.sessions[request.scope["cad2gis_token"]]
                if session.run_dir != key:
                    # Browser tabs share the login cookie. Retire the old page
                    # identity and its sockets before selecting another run.
                    for close in list(session.sockets):
                        await close()
                    session.review_id = secrets.token_hex(16)
                session.review = self.review_cache[key]
                session.run_dir = key
            return {"url": f"/?review={session.review_id}#console-app", "run_dir": str(directory),
                    "review_id": session.review_id, "districts": district_choices}

        @manager.post("/_app/api/shutdown")
        async def shutdown():
            if config.remote:
                return JSONResponse({"detail": "远程服务请由服务器管理员关闭"}, status_code=403)
            if self.jobs.active or self.agent.active:
                raise RuntimeError("请先等待或取消当前任务，再关闭程序")
            asyncio.get_running_loop().call_later(0.3, self.shutdown_callback)
            return {"status": "stopping"}

    @staticmethod
    async def body(request) -> dict[str, Any]:
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise ValueError("Request must use application/json")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 65536:
                raise ValueError("Request is too large")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid JSON request") from exc
        if not isinstance(value, dict):
            raise ValueError("Request must be an object")
        return value

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse, RedirectResponse

        if scope["type"] == "lifespan":
            return await self.manager(scope, receive, send)
        if scope["type"] not in {"http", "websocket"}:
            return
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope["headers"]}
        websocket = scope["type"] == "websocket"

        async def reject(code, detail):
            if websocket:
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": detail}, status_code=code)(scope, receive, send)

        try:
            host = headers.get("host", "")
            if _host_name(host) not in self.config.hosts:
                return await reject(400, "Host is not allowed")
        except ValueError:
            return await reject(400, "Invalid host")
        scheme = "https" if scope["scheme"] in {"https", "wss"} else "http"
        if self.config.remote and scheme != "https":
            return await reject(403, "HTTPS is required")
        if websocket or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
            if headers.get("origin") != f"{scheme}://{host}":
                return await reject(403, "Origin is not allowed")
        path = scope["path"]
        if not websocket and path == "/_app":
            return await RedirectResponse("/_app/")(scope, receive, send)
        public = (not websocket and (path == "/_app/" or path.startswith("/_app/assets/")
                                    or path == "/_app/api/login"))
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:
            pass
        token = cookie[self.cookie_name].value if self.cookie_name in cookie else ""
        session = self.sessions.get(token)
        if session is not None and session.expires <= time.monotonic():
            self.sessions.pop(token, None)
            session = None
        if not public and session is None:
            if not websocket and path == "/":
                return await RedirectResponse("/_app/")(scope, receive, send)
            return await reject(401, "请使用启动链接或服务器密码登录")
        scope["cad2gis_token"] = token
        if not websocket:
            async def safe_send(message):
                if message["type"] == "http.response.start":
                    extra = [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                             (b"referrer-policy", b"no-referrer")]
                    if path.startswith("/_app/"):
                        extra.append((b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"))
                    message = {**message, "headers": [*message.get("headers", []), *extra]}
                await send(message)
        else:
            safe_send = send
        if path.startswith("/_app/"):
            return await self.manager(scope, receive, safe_send)
        if session is None or session.review is None:
            if not websocket and path == "/":
                return await RedirectResponse("/_app/")(scope, receive, safe_send)
            return await reject(404, "请先从工作台打开转换结果")
        # Page identity is separate from authentication: another tab may change
        # this cookie's selected run while the old map still contains CAD picks.
        # Static assets remain shared; API fetches and sockets carry the identity
        # of the page that created them. Direct API writes must carry it too.
        query = parse_qs(scope.get("query_string", b"").decode("latin1"))
        review_id = headers.get("x-cad2gis-review", "") or query.get("review", [""])[0]
        changes_review = scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
        if ((review_id and review_id != session.review_id)
                or ((websocket or changes_review) and not review_id)):
            return await reject(409, "此页面的审查结果已切换，请从工作台重新打开结果")
        if not websocket and path == "/" and not review_id:
            return await RedirectResponse(f"/?review={session.review_id}#console-app")(scope, receive, safe_send)
        selected_review = session.review
        try:
            selected = self.config.path(session.run_dir, kind="directory")
            workspace = self.config.path(str(selected.parent / f"{selected.name}.review"), exists=False)
            self.config.admit_run(selected, workspace)
        except (ValueError, OSError) as exc:
            return await reject(400, str(exc))
        if websocket:
            from starlette.websockets import WebSocketDisconnect

            closed = False
            async def close():
                nonlocal closed
                if closed:
                    return
                closed = True
                try:
                    await send({"type": "websocket.close", "code": 1008})
                except (RuntimeError, OSError):
                    pass

            async def checked_send(message):
                if closed:
                    raise WebSocketDisconnect(code=1008)
                await send(message)

            async def expire():
                await asyncio.sleep(max(0, session.expires - time.monotonic()))
                await close()

            session.sockets.append(close)
            expiry = asyncio.create_task(expire())
            try:
                return await selected_review(scope, receive, checked_send)
            finally:
                expiry.cancel()
                session.sockets.remove(close)
        return await selected_review(scope, receive, safe_send)


def create_application(config: ApplicationConfig, *, review_factory=None) -> Application:
    return Application(config, review_factory=review_factory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CAD2GIS desktop and authenticated review application")
    parser.add_argument("--project-root", action="append", type=Path, help="Allowed server project directory (repeatable)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--public-host", action="append", default=[], help="Exact client-facing DNS name or IP (required for wildcard listeners)")
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--state-dir", type=Path, help="Private settings and conversation directory")
    args = parser.parse_args(argv)
    remote = args.host not in LOOPBACK
    secure = bool(args.ssl_certfile and args.ssl_keyfile)
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("--ssl-certfile and --ssl-keyfile must be used together")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    hosts = args.public_host or (list(LOOPBACK) if not remote else [args.host])
    roots = args.project_root or [Path.home() / "CAD2GIS Projects"]
    for root in roots:
        root.expanduser().mkdir(parents=True, exist_ok=True)
    try:
        state_dir = args.state_dir or ((Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CAD2GIS") if os.name == "nt"
                                      else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "cad2gis")
        config = ApplicationConfig(tuple(roots), tuple(hosts), remote=remote, secure=secure,
                                   password=os.environ.get("CAD2GIS_APP_PASSWORD", ""), state_dir=state_dir)
    except ValueError as exc:
        parser.error(str(exc))
    import uvicorn
    from .native_runtime import ensure_osgeo_runtime

    # Native DLL setup must precede the event loop / FastAPI worker threads.
    ensure_osgeo_runtime()
    app = create_application(config)
    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    listener = socket.create_server((args.host, args.port), family=family)
    port = listener.getsockname()[1]
    display_host = hosts[0] if remote else args.host
    try:
        if ipaddress.ip_address(display_host).version == 6:
            display_host = f"[{display_host}]"
    except ValueError:
        pass
    url = f"{'https' if secure else 'http'}://{display_host}:{port}/_app/"
    launch_url = url if remote else f"{url}#token={config.launch_token}"
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, proxy_headers=False,
        ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile, log_level="warning", access_log=False))
    app.shutdown_callback = lambda: setattr(server, "should_exit", True)

    def announce():
        if app.ready.wait(30):
            print(f"CAD2GIS: {launch_url}", flush=True)
            print("Close from the workspace, or press Ctrl+C to stop the service.", flush=True)
            if not args.no_browser and not remote:
                webbrowser.open(launch_url)

    threading.Thread(target=announce, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        app.jobs.close()
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
