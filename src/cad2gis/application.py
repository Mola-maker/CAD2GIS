"""Authenticated application shell around the canonical CLI and review service.

The shell owns processes and file admission, not CAD conversion rules. Desktop
and remote browser clients use the same service; MCP remains a separate entry.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
import ipaddress
import json
import os
from pathlib import Path
from pathlib import PureWindowsPath
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit
import webbrowser


COOKIE = "cad2gis_session"
SESSION_SECONDS = 12 * 60 * 60
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
ASSETS = Path(__file__).resolve().parent / "desktop"
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


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
                      "run_dir": run, "exit_code": None, "created": time.time()}
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
            with self.lock:
                if record["status"] == "cancelled" or self.closed:
                    record["status"] = "cancelled"
                    return
                self.process = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-u", "-X", "utf8", "-m", "cad2gis.cli", *args], cwd=str(self.config.roots[0]),
                    env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **hidden,
                )
                process = self.process
                record["status"] = "running"
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
                record["exit_code"] = code
                if record["status"] != "cancelled":
                    record["status"] = "succeeded" if code == 0 else "failed"
        except Exception as exc:
            with self.lock:
                record.update(status="failed", log=(record["log"] + f"\n无法运行任务：{exc}")[-131072:])
        finally:
            with self.lock:
                self.process = None
                self.active = ""

    def list(self):
        with self.lock:
            return [dict(record) for record in reversed(list(self.records.values()))]

    def cancel(self, job_id: str):
        with self.lock:
            if job_id != self.active:
                raise ValueError("任务已结束或不存在")
            self.records[job_id]["status"] = "cancelled"
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
            return {"status": "cancelled"}

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
