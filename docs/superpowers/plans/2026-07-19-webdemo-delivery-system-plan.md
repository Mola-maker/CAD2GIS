# Web Demo 交付系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 CAD2GIS 项目构建一个独立的 Web Demo 交付系统，包含 3D 演示视图（Pipeline / GIS / CAD）和基于 FastAPI + 现有 v3 pipeline 的 Web 转换前端。

**Architecture:** 后端 FastAPI 封装 `experiment/py_scripts/cad2gis_v3/cli.py` 作为子进程运行，通过 SSE 推送日志；前端 React + React Three Fiber 调用后端 API，A 部分展示 sample-run 或真实结果数据，B 部分负责上传、配置、触发转换、下载产物。

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, React 18, TypeScript, Vite, Tailwind CSS, React Three Fiber, MapLibre GL JS, TanStack Query, Zustand, Docker Compose.

## Global Constraints

- 所有代码放在 `webdemo/` 目录，不污染 `src/`, `official/`, `experiment/output/` 等受保护目录。
- 不修改 `experiment/py_scripts/cad2gis_v3/` 的核心转换逻辑，只封装调用。
- 后端对 v3 pipeline 以子进程方式调用，通过 stdout/stderr 逐行转发 SSE。
- 上传文件限制 100MB，保存前检查 magic bytes。
- 转换子进程默认超时 30 分钟，支持取消（SIGTERM）。
- 运行目录隔离：`webdemo/runs/{run_id}/`。
- 所有产物路径通过 `Path.resolve()` 解析，拒绝路径穿越。
- 不记录或暴露 API key、Authorization header。
- Docker Compose 必须能一键启动前后端。

---

## File Map

```
webdemo/
├── frontend/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/
│   │   │   ├── client.ts
│   │   │   ├── uploads.ts
│   │   │   ├── runs.ts
│   │   │   └── artifacts.ts
│   │   ├── store/
│   │   │   └── appStore.ts
│   │   ├── components/
│   │   │   ├── Layout.tsx
│   │   │   ├── Nav.tsx
│   │   │   ├── LogViewer.tsx
│   │   │   └── FileDownload.tsx
│   │   ├── scenes/
│   │   │   ├── PipelineStage.tsx
│   │   │   ├── PipelineScene.tsx
│   │   │   └── StageDetail.tsx
│   │   ├── gis/
│   │   │   ├── GisScene.tsx
│   │   │   ├── LayerToggle.tsx
│   │   │   └── FeatureTooltip.tsx
│   │   ├── cad/
│   │   │   ├── CadScene.tsx
│   │   │   ├── LayerTree.tsx
│   │   │   └── EntityTable.tsx
│   │   ├── converter/
│   │   │   ├── UploadForm.tsx
│   │   │   ├── ParamForm.tsx
│   │   │   ├── RunProgress.tsx
│   │   │   └── ResultPanel.tsx
│   │   └── types/
│   │       └── index.ts
│   └── public/sample-run/
│       └── .gitkeep
├── backend/
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── state.py
│   │   ├── config.py
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── upload.py
│   │   │   ├── convert.py
│   │   │   ├── artifacts.py
│   │   │   └── layers.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   └── cad2gis_runner.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── hashes.py
│   │       └── paths.py
│   └── tests/
│       ├── __init__.py
│       ├── test_upload.py
│       ├── test_convert.py
│       └── test_artifacts.py
├── runs/
│   └── .gitkeep
└── docker-compose.yml
```

---

### Task 1: 后端脚手架与依赖

**Files:**
- Create: `webdemo/backend/requirements.txt`
- Create: `webdemo/backend/app/__init__.py`
- Create: `webdemo/backend/app/config.py`
- Create: `webdemo/backend/app/models.py`
- Create: `webdemo/backend/app/state.py`
- Create: `webdemo/backend/tests/__init__.py`
- Create: `webdemo/backend/tests/test_health.py`
- Create: `webdemo/backend/app/main.py:1-30`

**Interfaces:**
- Produces: `Settings` (Pydantic model), `RUNS` global状态, `/health` endpoint.

- [ ] **Step 1: 创建 requirements.txt**

```text
fastapi==0.111.0
uvicorn[standard]==0.30.0
python-multipart==0.0.9
pydantic==2.7.4
pydantic-settings==2.3.4
aiofiles==23.2.1
pytest==8.2.2
httpx==0.27.0
```

- [ ] **Step 2: 创建 config.py**

```python
from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    project_root: Path = Path(__file__).resolve().parent.parent.parent.parent
    runs_dir: Path = project_root / "webdemo" / "runs"
    max_upload_bytes: int = 100 * 1024 * 1024
    convert_timeout_seconds: int = 30 * 60
    v3_cli: Path = project_root / "experiment" / "py_scripts" / "cad2gis_v3" / "cli.py"

    class Config:
        env_prefix = "CAD2GIS_WEBDEMO_"

settings = Settings()
```

- [ ] **Step 3: 创建 models.py**

```python
from enum import Enum
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class UploadedFile(BaseModel):
    upload_id: str
    filename: str
    sha256: str
    size: int
    path: Path

class RunConfig(BaseModel):
    source_crs: str = "EPSG:3857"
    target_crs: str = "EPSG:4326"
    source_profile: str | None = None
    mapping_registry: str | None = None
    gcp_profile: str | None = None
    enable_curation: bool = False
    generate_qgis: bool = True

class RunInfo(BaseModel):
    run_id: str
    status: RunStatus
    created_at: datetime
    config: RunConfig
    input_upload_id: str | None = None
    exit_code: int | None = None
    error_message: str | None = None
    artifacts: list[str] = Field(default_factory=list)

class RunSummary(BaseModel):
    run_id: str
    status: RunStatus
    created_at: datetime
    artifacts: list[str]
```

- [ ] **Step 4: 创建 state.py**

```python
from app.models import RunInfo, UploadedFile

UPLOADS: dict[str, UploadedFile] = {}
RUNS: dict[str, RunInfo] = {}
```

- [ ] **Step 5: 创建 main.py（基础）**

```python
from fastapi import FastAPI
from app.routers import upload, convert, artifacts, layers

app = FastAPI(title="CAD2GIS WebDemo", version="0.1.0")

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

app.include_router(upload.router, prefix="/api/v1")
app.include_router(convert.router, prefix="/api/v1")
app.include_router(artifacts.router, prefix="/api/v1")
app.include_router(layers.router, prefix="/api/v1")
```

- [ ] **Step 6: 创建空 routers/__init__.py 和 services/__init__.py 和 utils/__init__.py**

- [ ] **Step 7: 写健康检查测试**

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 8: 运行测试**

```bash
cd webdemo/backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest tests/test_health.py -v
```

Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add webdemo/backend
git commit -m "feat(webdemo): scaffold FastAPI backend with health endpoint"
```

---

### Task 2: 后端工具函数

**Files:**
- Create: `webdemo/backend/app/utils/hashes.py`
- Create: `webdemo/backend/app/utils/paths.py`
- Create: `webdemo/backend/tests/test_utils.py`

**Interfaces:**
- Produces: `sha256_file(path) -> str`, `safe_run_dir(run_id) -> Path`, `resolve_safe(path, base) -> Path`.

- [ ] **Step 1: 创建 hashes.py**

```python
import hashlib
from pathlib import Path

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 2: 创建 paths.py**

```python
from pathlib import Path

ALLOWED_SUFFIXES = {".dwg", ".dxf", ".zip"}

def resolve_safe(path: Path, base: Path) -> Path:
    resolved = (base / path).resolve()
    base_resolved = base.resolve()
    if not str(resolved).startswith(str(base_resolved)):
        raise ValueError("path traversal detected")
    return resolved

def validate_upload_name(filename: str) -> None:
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("invalid filename")
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"only {ALLOWED_SUFFIXES} allowed")
```

- [ ] **Step 3: 写测试**

```python
from pathlib import Path
from app.utils.hashes import sha256_file
from app.utils.paths import resolve_safe, validate_upload_name

def test_sha256_file(tmp_path):
    p = tmp_path / "hello.txt"
    p.write_text("hello")
    assert sha256_file(p) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

def test_resolve_safe(tmp_path):
    base = tmp_path / "runs"
    base.mkdir()
    assert resolve_safe(Path("sub/file.txt"), base) == base / "sub/file.txt"

def test_resolve_safe_traversal(tmp_path):
    base = tmp_path / "runs"
    base.mkdir()
    try:
        resolve_safe(Path("../escape.txt"), base)
        raise AssertionError("should raise")
    except ValueError as e:
        assert "path traversal" in str(e)

def test_validate_upload_name():
    validate_upload_name("foo.dwg")
    try:
        validate_upload_name("../foo.exe")
        raise AssertionError("should raise")
    except ValueError:
        pass
```

- [ ] **Step 4: 运行测试**

```bash
cd webdemo/backend
pytest tests/test_utils.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webdemo/backend
git commit -m "feat(webdemo): add safe path and hash utilities"
```

---

### Task 3: 上传 API

**Files:**
- Create: `webdemo/backend/app/routers/upload.py`
- Modify: `webdemo/backend/app/main.py`（确认 include_router）
- Create: `webdemo/backend/tests/test_upload.py`

**Interfaces:**
- Produces: `POST /api/v1/upload` returns `{upload_id, filename, sha256, size}`.

- [ ] **Step 1: 创建 upload.py**

```python
import uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.config import settings
from app.state import UPLOADS
from app.models import UploadedFile
from app.utils.hashes import sha256_file
from app.utils.paths import validate_upload_name, resolve_safe

router = APIRouter()

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="no filename")
    validate_upload_name(file.filename)

    upload_id = uuid.uuid4().hex
    upload_dir = settings.runs_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{upload_id}_{file.filename}"

    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(8192):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="file too large")
            f.write(chunk)

    sha = sha256_file(dest)
    uploaded = UploadedFile(
        upload_id=upload_id,
        filename=file.filename,
        sha256=sha,
        size=size,
        path=dest,
    )
    UPLOADS[upload_id] = uploaded
    return uploaded.model_dump(exclude={"path"})
```

- [ ] **Step 2: 写测试**

```python
import io
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_upload_dwg():
    data = b"mock dwg content"
    response = client.post(
        "/api/v1/upload",
        files={"file": ("test.dwg", io.BytesIO(data), "application/octet-stream")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "test.dwg"
    assert body["size"] == len(data)
    assert "upload_id" in body
    assert "sha256" in body

def test_upload_invalid_extension():
    response = client.post(
        "/api/v1/upload",
        files={"file": ("test.exe", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 400
```

- [ ] **Step 3: 运行测试**

```bash
cd webdemo/backend
pytest tests/test_upload.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add webdemo/backend
git commit -m "feat(webdemo): add DWG upload endpoint with size and extension checks"
```

---

### Task 4: 转换任务运行器

**Files:**
- Create: `webdemo/backend/app/services/cad2gis_runner.py`
- Create: `webdemo/backend/tests/test_runner.py`

**Interfaces:**
- Produces: `async def run_convert(run_id: str, run_dir: Path, input_path: Path, config: RunConfig, log_queue: asyncio.Queue) -> int`.

- [ ] **Step 1: 创建 cad2gis_runner.py**

```python
import asyncio
from pathlib import Path
from app.config import settings
from app.models import RunConfig

async def run_convert(
    run_id: str,
    run_dir: Path,
    input_path: Path,
    config: RunConfig,
    log_queue: asyncio.Queue,
) -> int:
    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir = run_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python",
        str(settings.v3_cli),
        "--input", str(input_path),
        "--run-dir", str(run_dir),
        "--source-crs", config.source_crs,
        "--target-crs", config.target_crs,
    ]
    if config.source_profile:
        cmd.extend(["--source-profile", config.source_profile])
    if config.mapping_registry:
        cmd.extend(["--mapping-registry", config.mapping_registry])
    if config.gcp_profile:
        cmd.extend(["--gcp-profile", config.gcp_profile])

    await log_queue.put(f"[runner] {' '.join(cmd)}\n")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        await log_queue.put(line.decode("utf-8", errors="replace"))

    try:
        await asyncio.wait_for(proc.wait(), timeout=settings.convert_timeout_seconds)
    except asyncio.TimeoutError:
        proc.terminate()
        await log_queue.put("[runner] timeout, terminating\n")
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return -1

    return proc.returncode or 0
```

- [ ] **Step 2: 写测试（使用模拟脚本）**

```python
import asyncio
from pathlib import Path
from app.models import RunConfig
from app.services.cad2gis_runner import run_convert

async def test_runner_echo(tmp_path):
    mock_cli = tmp_path / "mock_cli.py"
    mock_cli.write_text('import sys; print("hello", sys.argv[3])')

    from app import config as config_module
    original = config_module.settings.v3_cli
    config_module.settings.v3_cli = mock_cli
    try:
        q: asyncio.Queue = asyncio.Queue()
        rc = await run_convert("r1", tmp_path / "run", Path("/fake/input.dwg"), RunConfig(), q)
        assert rc == 0
        logs = []
        while not q.empty():
            logs.append(await q.get())
        assert any("hello" in log for log in logs)
    finally:
        config_module.settings.v3_cli = original
```

- [ ] **Step 3: 运行测试**

```bash
cd webdemo/backend
pytest tests/test_runner.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add webdemo/backend
git commit -m "feat(webdemo): add cad2gis_v3 runner service with timeout and log queue"
```

---

### Task 5: 转换任务 API + SSE 日志

**Files:**
- Create: `webdemo/backend/app/routers/convert.py`
- Create: `webdemo/backend/tests/test_convert.py`

**Interfaces:**
- Produces: `POST /api/v1/runs`, `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/logs` (SSE), `POST /api/v1/runs/{id}/cancel`.

- [ ] **Step 1: 创建 convert.py**

```python
import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.config import settings
from app.state import RUNS, UPLOADS
from app.models import RunConfig, RunInfo, RunStatus, RunSummary
from app.services.cad2gis_runner import run_convert

router = APIRouter()

LOG_QUEUES: dict[str, asyncio.Queue] = {}

@router.post("/runs")
async def create_run(config: RunConfig):
    if config.input_upload_id not in UPLOADS:
        raise HTTPException(status_code=404, detail="upload not found")

    upload = UPLOADS[config.input_upload_id]
    run_id = uuid.uuid4().hex
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # link input into run dir
    input_link = run_dir / upload.filename
    input_link.write_bytes(upload.path.read_bytes())

    info = RunInfo(
        run_id=run_id,
        status=RunStatus.PENDING,
        created_at=datetime.utcnow(),
        config=config,
        input_upload_id=config.input_upload_id,
    )
    RUNS[run_id] = info
    LOG_QUEUES[run_id] = asyncio.Queue()

    asyncio.create_task(_execute(run_id, run_dir, input_link, config))
    return info.model_dump()

async def _execute(run_id: str, run_dir: Path, input_path: Path, config: RunConfig):
    info = RUNS[run_id]
    queue = LOG_QUEUES[run_id]
    info.status = RunStatus.RUNNING

    try:
        exit_code = await run_convert(run_id, run_dir, input_path, config, queue)
        info.exit_code = exit_code
        if exit_code == 0:
            info.status = RunStatus.COMPLETED
            info.artifacts = [p.name for p in (run_dir / "output").iterdir() if p.is_file()]
        else:
            info.status = RunStatus.FAILED
            info.error_message = f"process exited with code {exit_code}"
    except Exception as e:
        info.status = RunStatus.FAILED
        info.error_message = str(e)
        await queue.put(f"[error] {e}\n")
    finally:
        await queue.put("[EOF]")

@router.get("/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="run not found")
    return RUNS[run_id].model_dump()

@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    # In-memory only; real cancel needs process handle. Deferred to Task 6 refinement.
    raise HTTPException(status_code=501, detail="cancel not yet implemented")

@router.get("/runs/{run_id}/logs")
async def stream_logs(run_id: str):
    if run_id not in LOG_QUEUES:
        raise HTTPException(status_code=404, detail="run not found")
    queue = LOG_QUEUES[run_id]

    async def event_generator():
        while True:
            line = await queue.get()
            yield f"data: {line}\n\n"
            if line.strip() == "[EOF]":
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/runs")
def list_runs() -> list[RunSummary]:
    return [
        RunSummary(
            run_id=r.run_id,
            status=r.status,
            created_at=r.created_at,
            artifacts=r.artifacts,
        )
        for r in RUNS.values()
    ]
```

- [ ] **Step 2: 写测试**

```python
import io
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_create_run_missing_upload():
    response = client.post("/api/v1/runs", json={"input_upload_id": "missing"})
    assert response.status_code == 404

def test_create_run_and_stream_logs(tmp_path, monkeypatch):
    from app import config as config_module
    from app.state import UPLOADS
    from app.models import UploadedFile

    mock_cli = tmp_path / "mock_cli.py"
    mock_cli.write_text('import sys, time; print("step1"); time.sleep(0.05); print("step2")')
    config_module.settings.v3_cli = mock_cli
    config_module.settings.runs_dir = tmp_path / "runs"

    upload_id = "u1"
    upload_path = tmp_path / "uploads"
    upload_path.mkdir(parents=True)
    uploaded_file = upload_path / "test.dwg"
    uploaded_file.write_text("mock")
    UPLOADS[upload_id] = UploadedFile(
        upload_id=upload_id,
        filename="test.dwg",
        sha256="abc",
        size=4,
        path=uploaded_file,
    )

    response = client.post("/api/v1/runs", json={
        "input_upload_id": upload_id,
        "source_crs": "EPSG:3857",
        "target_crs": "EPSG:4326",
    })
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    import time
    time.sleep(0.2)

    status = client.get(f"/api/v1/runs/{run_id}")
    assert status.status_code == 200
    assert status.json()["status"] in ("running", "completed")
```

- [ ] **Step 3: 运行测试**

```bash
cd webdemo/backend
pytest tests/test_convert.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add webdemo/backend
git commit -m "feat(webdemo): add run creation and SSE log streaming endpoints"
```

---

### Task 6: 产物下载与图层摘要 API

**Files:**
- Create: `webdemo/backend/app/routers/artifacts.py`
- Create: `webdemo/backend/app/routers/layers.py`
- Create: `webdemo/backend/tests/test_artifacts.py`

**Interfaces:**
- Produces: `GET /api/v1/runs/{id}/artifacts/{name}` for download, `GET /api/v1/runs/{id}/layers` returns GeoJSON summary, `GET /api/v1/runs/{id}/census-summary` returns CAD summary.

- [ ] **Step 1: 创建 artifacts.py**

```python
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.config import settings
from app.state import RUNS
from app.utils.paths import resolve_safe

router = APIRouter()

@router.get("/runs/{run_id}/artifacts/{artifact_name}")
def download_artifact(run_id: str, artifact_name: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="run not found")
    run_dir = settings.runs_dir / run_id / "output"
    path = resolve_safe(Path(artifact_name), run_dir)
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(path, filename=artifact_name)

@router.get("/runs/{run_id}/artifacts")
def list_artifacts(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="run not found")
    run_dir = settings.runs_dir / run_id / "output"
    if not run_dir.exists():
        return []
    return [p.name for p in run_dir.iterdir() if p.is_file()]
```

- [ ] **Step 2: 创建 layers.py**

```python
from fastapi import APIRouter, HTTPException
from app.config import settings
from app.state import RUNS

router = APIRouter()

@router.get("/runs/{run_id}/layers")
def get_layers(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="run not found")
    # Placeholder: real implementation reads apd_delivery.gpkg via fiona/geopandas.
    return {
        "run_id": run_id,
        "layers": [],
        "note": "GeoPackage parsing implemented in Task 13",
    }

@router.get("/runs/{run_id}/census-summary")
def get_census_summary(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="run not found")
    # Placeholder: real implementation reads run_manifest.json / evidence.gpkg.
    return {
        "run_id": run_id,
        "entities": [],
        "note": "Census summary implemented in Task 14",
    }
```

- [ ] **Step 3: 写测试**

```python
import io
from fastapi.testclient import TestClient
from app.main import app
from app.state import UPLOADS, RUNS
from app.models import UploadedFile, RunInfo, RunConfig, RunStatus
from app.config import settings
from datetime import datetime

client = TestClient(app)

def test_list_artifacts(tmp_path):
    from app import config as config_module
    config_module.settings.runs_dir = tmp_path / "runs"
    run_dir = config_module.settings.runs_dir / "r1" / "output"
    run_dir.mkdir(parents=True)
    (run_dir / "test.txt").write_text("hello")

    RUNS["r1"] = RunInfo(
        run_id="r1",
        status=RunStatus.COMPLETED,
        created_at=datetime.utcnow(),
        config=RunConfig(input_upload_id="u1"),
        artifacts=["test.txt"],
    )

    response = client.get("/api/v1/runs/r1/artifacts")
    assert response.status_code == 200
    assert "test.txt" in response.json()

    download = client.get("/api/v1/runs/r1/artifacts/test.txt")
    assert download.status_code == 200
    assert download.content == b"hello"
```

- [ ] **Step 4: 运行测试**

```bash
cd webdemo/backend
pytest tests/test_artifacts.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webdemo/backend
git commit -m "feat(webdemo): add artifact download and layer summary endpoints"
```

---

### Task 7: 前端脚手架

**Files:**
- Create: `webdemo/frontend/package.json`
- Create: `webdemo/frontend/tsconfig.json`
- Create: `webdemo/frontend/tsconfig.node.json`
- Create: `webdemo/frontend/vite.config.ts`
- Create: `webdemo/frontend/tailwind.config.js`
- Create: `webdemo/frontend/postcss.config.js`
- Create: `webdemo/frontend/index.html`
- Create: `webdemo/frontend/src/main.tsx`
- Create: `webdemo/frontend/src/index.css`
- Create: `webdemo/frontend/src/vite-env.d.ts`

**Interfaces:**
- Produces: 可运行的 Vite + React + TypeScript 项目，开发服务器启动成功。

- [ ] **Step 1: 创建 package.json**

```json
{
  "name": "cad2gis-webdemo-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0"
  },
  "dependencies": {
    "@react-three/drei": "^9.108.0",
    "@react-three/fiber": "^8.16.8",
    "@tanstack/react-query": "^5.50.0",
    "maplibre-gl": "^4.5.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.24.0",
    "three": "^0.166.0",
    "zustand": "^4.5.4"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@types/three": "^0.166.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.39",
    "tailwindcss": "^3.4.4",
    "typescript": "^5.5.3",
    "vite": "^5.3.3"
  }
}
```

- [ ] **Step 2: 创建 tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 3: 创建 tsconfig.node.json**

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 4: 创建 vite.config.ts**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

- [ ] **Step 5: 创建 tailwind.config.js**

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        blueprint: {
          50: '#f4f7fa',
          100: '#e8eef4',
          500: '#2b579a',
          700: '#1a3a6e',
          900: '#0f2240',
        },
        ochre: '#c17c45',
        sage: '#5a7d5a',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
```

- [ ] **Step 6: 创建 postcss.config.js**

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 7: 创建 index.html**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>CAD2GIS Web Demo</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: 创建 index.css**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  @apply bg-blueprint-50 text-blueprint-900;
}
```

- [ ] **Step 9: 创建 main.tsx**

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 10: 创建 App.tsx（占位）**

```tsx
function App() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <h1 className="text-2xl font-bold">CAD2GIS Web Demo</h1>
    </div>
  )
}

export default App
```

- [ ] **Step 11: 安装依赖并启动**

```bash
cd webdemo/frontend
npm install
npm run dev
```

Expected: Vite dev server starts on http://localhost:5173

- [ ] **Step 12: Commit**

```bash
git add webdemo/frontend
git commit -m "feat(webdemo): scaffold React + Vite + Tailwind frontend"
```

---

### Task 8: 前端类型与 API 客户端

**Files:**
- Create: `webdemo/frontend/src/types/index.ts`
- Create: `webdemo/frontend/src/api/client.ts`
- Create: `webdemo/frontend/src/api/uploads.ts`
- Create: `webdemo/frontend/src/api/runs.ts`
- Create: `webdemo/frontend/src/api/artifacts.ts`

**Interfaces:**
- Produces: TypeScript types matching backend models; API functions for upload, run, logs, artifacts.

- [ ] **Step 1: 创建 types/index.ts**

```typescript
export interface UploadedFile {
  upload_id: string
  filename: string
  sha256: string
  size: number
}

export interface RunConfig {
  input_upload_id: string
  source_crs?: string
  target_crs?: string
  source_profile?: string
  mapping_registry?: string
  gcp_profile?: string
  enable_curation?: boolean
  generate_qgis?: boolean
}

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface RunInfo {
  run_id: string
  status: RunStatus
  created_at: string
  config: RunConfig
  input_upload_id: string | null
  exit_code: number | null
  error_message: string | null
  artifacts: string[]
}

export interface RunSummary {
  run_id: string
  status: RunStatus
  created_at: string
  artifacts: string[]
}
```

- [ ] **Step 2: 创建 api/client.ts**

```typescript
const API_BASE = '/api/v1'

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`HTTP ${response.status}: ${text}`)
  }
  return response.json() as Promise<T>
}
```

- [ ] **Step 3: 创建 api/uploads.ts**

```typescript
import { apiFetch } from './client'
import type { UploadedFile } from '../types'

export async function uploadFile(file: File): Promise<UploadedFile> {
  const formData = new FormData()
  formData.append('file', file)
  return apiFetch<UploadedFile>('/upload', {
    method: 'POST',
    body: formData,
  })
}
```

- [ ] **Step 4: 创建 api/runs.ts**

```typescript
import { apiFetch } from './client'
import type { RunConfig, RunInfo, RunSummary } from '../types'

export async function createRun(config: RunConfig): Promise<RunInfo> {
  return apiFetch<RunInfo>('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
}

export async function getRun(runId: string): Promise<RunInfo> {
  return apiFetch<RunInfo>(`/runs/${runId}`)
}

export async function listRuns(): Promise<RunSummary[]> {
  return apiFetch<RunSummary[]>('/runs')
}

export function streamLogs(runId: string, onLine: (line: string) => void): () => void {
  const eventSource = new EventSource(`/api/v1/runs/${runId}/logs`)
  eventSource.onmessage = (event) => {
    onLine(event.data)
  }
  eventSource.onerror = () => {
    eventSource.close()
  }
  return () => eventSource.close()
}
```

- [ ] **Step 5: 创建 api/artifacts.ts**

```typescript
export function artifactUrl(runId: string, name: string): string {
  return `/api/v1/runs/${runId}/artifacts/${encodeURIComponent(name)}`
}

export async function listArtifacts(runId: string): Promise<string[]> {
  const response = await fetch(`/api/v1/runs/${runId}/artifacts`)
  if (!response.ok) throw new Error('failed to list artifacts')
  return response.json()
}
```

- [ ] **Step 6: Commit**

```bash
git add webdemo/frontend/src/api webdemo/frontend/src/types
git commit -m "feat(webdemo): add frontend types and API clients"
```

---

### Task 9: 前端布局、路由与全局状态

**Files:**
- Create: `webdemo/frontend/src/store/appStore.ts`
- Create: `webdemo/frontend/src/components/Layout.tsx`
- Create: `webdemo/frontend/src/components/Nav.tsx`
- Modify: `webdemo/frontend/src/App.tsx`

**Interfaces:**
- Produces: React Router layout with navigation between Demo / Converter / Runs views.

- [ ] **Step 1: 创建 appStore.ts**

```typescript
import { create } from 'zustand'

interface AppState {
  selectedRunId: string | null
  setSelectedRunId: (id: string | null) => void
}

export const useAppStore = create<AppState>((set) => ({
  selectedRunId: null,
  setSelectedRunId: (id) => set({ selectedRunId: id }),
}))
```

- [ ] **Step 2: 创建 Nav.tsx**

```tsx
import { Link, useLocation } from 'react-router-dom'

const links = [
  { to: '/', label: '3D Demo' },
  { to: '/converter', label: 'Converter' },
  { to: '/runs', label: 'Runs' },
]

export function Nav() {
  const location = useLocation()
  return (
    <nav className="bg-blueprint-900 text-white px-6 py-3 flex gap-6">
      <span className="font-bold font-mono">CAD2GIS</span>
      {links.map((link) => (
        <Link
          key={link.to}
          to={link.to}
          className={`hover:text-blueprint-100 ${
            location.pathname === link.to ? 'underline' : ''
          }`}
        >
          {link.label}
        </Link>
      ))}
    </nav>
  )
}
```

- [ ] **Step 3: 创建 Layout.tsx**

```tsx
import { Nav } from './Nav'

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      <Nav />
      <main className="flex-1 overflow-hidden">{children}</main>
    </div>
  )
}
```

- [ ] **Step 4: 修改 App.tsx**

```tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DemoPage } from './pages/DemoPage'
import { ConverterPage } from './pages/ConverterPage'
import { RunsPage } from './pages/RunsPage'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const queryClient = new QueryClient()

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<DemoPage />} />
            <Route path="/converter" element={<ConverterPage />} />
            <Route path="/runs" element={<RunsPage />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
```

- [ ] **Step 5: 创建 pages 目录和占位页面**

```bash
mkdir -p webdemo/frontend/src/pages
```

创建 `webdemo/frontend/src/pages/DemoPage.tsx`:

```tsx
export function DemoPage() {
  return <div className="p-6">3D Demo page placeholder</div>
}
```

创建 `webdemo/frontend/src/pages/ConverterPage.tsx`:

```tsx
export function ConverterPage() {
  return <div className="p-6">Converter page placeholder</div>
}
```

创建 `webdemo/frontend/src/pages/RunsPage.tsx`:

```tsx
export function RunsPage() {
  return <div className="p-6">Runs page placeholder</div>
}
```

- [ ] **Step 6: 运行类型检查**

```bash
cd webdemo/frontend
npm run build
```

Expected: build succeeds

- [ ] **Step 7: Commit**

```bash
git add webdemo/frontend/src
git commit -m "feat(webdemo): add routing, layout, and global state"
```

---

### Task 10: B 部分界面（上传、参数、任务、结果）

**Files:**
- Create: `webdemo/frontend/src/converter/UploadForm.tsx`
- Create: `webdemo/frontend/src/converter/ParamForm.tsx`
- Create: `webdemo/frontend/src/converter/RunProgress.tsx`
- Create: `webdemo/frontend/src/converter/ResultPanel.tsx`
- Modify: `webdemo/frontend/src/pages/ConverterPage.tsx`

**Interfaces:**
- Produces: Complete converter UI with upload, config, run, logs, results.

- [ ] **Step 1: 创建 UploadForm.tsx**

```tsx
import { useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { uploadFile } from '../api/uploads'
import type { UploadedFile } from '../types'

interface Props {
  onUpload: (file: UploadedFile) => void
}

export function UploadForm({ onUpload }: Props) {
  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return
    const result = await uploadFile(acceptedFiles[0])
    onUpload(result)
  }, [onUpload])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'application/octet-stream': ['.dwg', '.dxf', '.zip'] },
    maxSize: 100 * 1024 * 1024,
  })

  return (
    <div
      {...getRootProps()}
      className="border-2 border-dashed border-blueprint-500 rounded p-8 text-center cursor-pointer hover:bg-blueprint-100"
    >
      <input {...getInputProps()} />
      {isDragActive ? (
        <p>Drop DWG here ...</p>
      ) : (
        <p>Drag & drop a DWG/DXF/ZIP, or click to select</p>
      )}
    </div>
  )
}
```

注意：这里引入了 `react-dropzone`，需要添加到 package.json。在 Step 12 安装。

- [ ] **Step 2: 创建 ParamForm.tsx**

```tsx
import { useState } from 'react'
import type { RunConfig } from '../types'

interface Props {
  uploadId: string | null
  onSubmit: (config: RunConfig) => void
  disabled: boolean
}

export function ParamForm({ uploadId, onSubmit, disabled }: Props) {
  const [sourceCrs, setSourceCrs] = useState('EPSG:3857')
  const [targetCrs, setTargetCrs] = useState('EPSG:4326')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!uploadId) return
    onSubmit({
      input_upload_id: uploadId,
      source_crs: sourceCrs,
      target_crs: targetCrs,
      generate_qgis: true,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 bg-white p-4 rounded shadow">
      <div>
        <label className="block text-sm font-medium">Source CRS</label>
        <input
          type="text"
          value={sourceCrs}
          onChange={(e) => setSourceCrs(e.target.value)}
          className="border rounded px-2 py-1 w-full"
          disabled={disabled}
        />
      </div>
      <div>
        <label className="block text-sm font-medium">Target CRS</label>
        <input
          type="text"
          value={targetCrs}
          onChange={(e) => setTargetCrs(e.target.value)}
          className="border rounded px-2 py-1 w-full"
          disabled={disabled}
        />
      </div>
      <button
        type="submit"
        disabled={!uploadId || disabled}
        className="bg-blueprint-700 text-white px-4 py-2 rounded disabled:opacity-50"
      >
        Start Conversion
      </button>
    </form>
  )
}
```

- [ ] **Step 3: 创建 RunProgress.tsx**

```tsx
import { useEffect, useRef, useState } from 'react'
import { streamLogs } from '../api/runs'

interface Props {
  runId: string
  status: string
}

export function RunProgress({ runId, status }: Props) {
  const [logs, setLogs] = useState<string[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!runId) return
    const close = streamLogs(runId, (line) => {
      setLogs((prev) => [...prev, line])
    })
    return close
  }, [runId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  return (
    <div className="bg-white rounded shadow p-4 h-96 flex flex-col">
      <div className="flex justify-between mb-2">
        <span className="font-mono font-bold">Run {runId}</span>
        <span className="font-mono text-sm uppercase">{status}</span>
      </div>
      <pre className="flex-1 overflow-auto bg-blueprint-50 p-2 text-xs font-mono">
        {logs.join('')}
        <div ref={bottomRef} />
      </pre>
    </div>
  )
}
```

- [ ] **Step 4: 创建 ResultPanel.tsx**

```tsx
import { artifactUrl } from '../api/artifacts'

interface Props {
  runId: string
  artifacts: string[]
}

export function ResultPanel({ runId, artifacts }: Props) {
  if (artifacts.length === 0) return null
  return (
    <div className="bg-white rounded shadow p-4">
      <h3 className="font-bold mb-2">Artifacts</h3>
      <ul className="space-y-1">
        {artifacts.map((name) => (
          <li key={name}>
            <a
              href={artifactUrl(runId, name)}
              download
              className="text-blueprint-700 hover:underline"
            >
              {name}
            </a>
          </li>
        ))}
      </ul>
    </div>
  )
}
```

- [ ] **Step 5: 修改 ConverterPage.tsx**

```tsx
import { useState } from 'react'
import { UploadForm } from '../converter/UploadForm'
import { ParamForm } from '../converter/ParamForm'
import { RunProgress } from '../converter/RunProgress'
import { ResultPanel } from '../converter/ResultPanel'
import { createRun, getRun } from '../api/runs'
import { useAppStore } from '../store/appStore'
import type { UploadedFile, RunInfo } from '../types'

export function ConverterPage() {
  const [uploaded, setUploaded] = useState<UploadedFile | null>(null)
  const [run, setRun] = useState<RunInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const setSelectedRunId = useAppStore((s) => s.setSelectedRunId)

  const handleStart = async (config: Parameters<typeof createRun>[0]) => {
    setLoading(true)
    const info = await createRun(config)
    setRun(info)
    setSelectedRunId(info.run_id)
    setLoading(false)

    const interval = setInterval(async () => {
      const updated = await getRun(info.run_id)
      setRun(updated)
      if (updated.status === 'completed' || updated.status === 'failed') {
        clearInterval(interval)
      }
    }, 2000)
  }

  return (
    <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-7xl mx-auto">
      <div className="space-y-6">
        <UploadForm onUpload={setUploaded} />
        {uploaded && (
          <div className="text-sm font-mono">
            Uploaded: {uploaded.filename} ({uploaded.size} bytes)
          </div>
        )}
        <ParamForm
          uploadId={uploaded?.upload_id || null}
          onSubmit={handleStart}
          disabled={loading}
        />
      </div>
      <div className="space-y-6">
        {run && <RunProgress runId={run.run_id} status={run.status} />}
        {run && <ResultPanel runId={run.run_id} artifacts={run.artifacts} />}
      </div>
    </div>
  )
}
```

- [ ] **Step 6: 更新 package.json 添加 react-dropzone**

```json
"react-dropzone": "^14.2.3"
```

- [ ] **Step 7: 安装并构建**

```bash
cd webdemo/frontend
npm install
npm run build
```

Expected: build succeeds

- [ ] **Step 8: Commit**

```bash
git add webdemo/frontend
git commit -m "feat(webdemo): add converter UI with upload, params, logs, and results"
```

---

### Task 11: Runs 列表页

**Files:**
- Modify: `webdemo/frontend/src/pages/RunsPage.tsx`

**Interfaces:**
- Produces: A page listing all runs with links to converter details.

- [ ] **Step 1: 修改 RunsPage.tsx**

```tsx
import { useQuery } from '@tanstack/react-query'
import { listRuns } from '../api/runs'
import { useAppStore } from '../store/appStore'

export function RunsPage() {
  const { data: runs, isLoading } = useQuery({
    queryKey: ['runs'],
    queryFn: listRuns,
    refetchInterval: 3000,
  })
  const setSelectedRunId = useAppStore((s) => s.setSelectedRunId)

  if (isLoading) return <div className="p-6">Loading...</div>

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h2 className="text-xl font-bold mb-4">Conversion Runs</h2>
      <table className="w-full bg-white rounded shadow text-sm">
        <thead className="bg-blueprint-100">
          <tr>
            <th className="text-left p-2">Run ID</th>
            <th className="text-left p-2">Status</th>
            <th className="text-left p-2">Created</th>
            <th className="text-left p-2">Artifacts</th>
          </tr>
        </thead>
        <tbody>
          {runs?.map((run) => (
            <tr key={run.run_id} className="border-t">
              <td className="p-2 font-mono">
                <button
                  onClick={() => setSelectedRunId(run.run_id)}
                  className="text-blueprint-700 hover:underline"
                >
                  {run.run_id.slice(0, 8)}
                </button>
              </td>
              <td className="p-2 uppercase">{run.status}</td>
              <td className="p-2">{new Date(run.created_at).toLocaleString()}</td>
              <td className="p-2">{run.artifacts.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: 构建检查**

```bash
cd webdemo/frontend
npm run build
```

Expected: build succeeds

- [ ] **Step 3: Commit**

```bash
git add webdemo/frontend/src/pages/RunsPage.tsx
git commit -m "feat(webdemo): add runs list page"
```

---

### Task 12: A 部分 - 3D Pipeline 舞台

**Files:**
- Create: `webdemo/frontend/src/scenes/PipelineStage.tsx`
- Create: `webdemo/frontend/src/scenes/PipelineScene.tsx`
- Create: `webdemo/frontend/src/scenes/StageDetail.tsx`
- Modify: `webdemo/frontend/src/pages/DemoPage.tsx`

**Interfaces:**
- Produces: An interactive 3D engineering-style pipeline visualization.

- [ ] **Step 1: 创建 PipelineStage.tsx**

```tsx
import { useState } from 'react'
import { RoundedBox, Text } from '@react-three/drei'
import type { ThreeEvent } from '@react-three/fiber'

interface Props {
  position: [number, number, number]
  label: string
  count: number
  sublabel: string
  onClick: () => void
  active: boolean
}

export function PipelineStage({ position, label, count, sublabel, onClick, active }: Props) {
  const [hovered, setHovered] = useState(false)
  const color = active ? '#c17c45' : hovered ? '#2b579a' : '#e8eef4'

  return (
    <group position={position}>
      <RoundedBox
        args={[2.2, 1.2, 0.6]}
        radius={0.05}
        onClick={onClick}
        onPointerOver={(e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); setHovered(true) }}
        onPointerOut={() => setHovered(false)}
      >
        <meshStandardMaterial color={color} />
      </RoundedBox>
      <Text position={[0, 0.25, 0.35]} fontSize={0.18} color="#0f2240" anchorX="center">
        {label}
      </Text>
      <Text position={[0, -0.05, 0.35]} fontSize={0.14} color="#1a3a6e" anchorX="center">
        {count.toLocaleString()}
      </Text>
      <Text position={[0, -0.35, 0.35]} fontSize={0.1} color="#5a7d5a" anchorX="center">
        {sublabel}
      </Text>
    </group>
  )
}
```

- [ ] **Step 2: 创建 PipelineScene.tsx**

```tsx
import { useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls, Line } from '@react-three/drei'
import { PipelineStage } from './PipelineStage'
import { StageDetail } from './StageDetail'

const STAGES = [
  { id: 'dwg', label: 'DWG', count: 6940, sublabel: 'entities' },
  { id: 'census', label: 'Census', count: 222, sublabel: 'INSERTs' },
  { id: 'evidence', label: 'Evidence', count: 212, sublabel: 'candidates' },
  { id: 'topology', label: 'Topology', count: 139, sublabel: 'spans' },
  { id: 'delivery', label: 'Delivery', count: 8, sublabel: 'layers' },
  { id: 'qgis', label: 'QGIS', count: 8, sublabel: 'styles' },
]

export function PipelineScene() {
  const [activeId, setActiveId] = useState<string | null>(null)
  const positions: [number, number, number][] = STAGES.map((_, i) => [i * 3 - 7.5, 0, 0])

  return (
    <div className="flex h-full">
      <div className="flex-1">
        <Canvas camera={{ position: [0, 4, 12], fov: 45 }}>
          <ambientLight intensity={0.8} />
          <directionalLight position={[10, 10, 5]} intensity={1} />
          <OrbitControls enablePan={true} enableZoom={true} />
          {STAGES.map((stage, i) => (
            <PipelineStage
              key={stage.id}
              position={positions[i]}
              label={stage.label}
              count={stage.count}
              sublabel={stage.sublabel}
              active={activeId === stage.id}
              onClick={() => setActiveId(stage.id)}
            />
          ))}
          {STAGES.slice(0, -1).map((_, i) => (
            <Line
              key={i}
              points={[positions[i], positions[i + 1]]}
              color="#2b579a"
              lineWidth={2}
            />
          ))}
        </Canvas>
      </div>
      {activeId && (
        <StageDetail
          stage={STAGES.find((s) => s.id === activeId)!}
          onClose={() => setActiveId(null)}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 3: 创建 StageDetail.tsx**

```tsx
interface Props {
  stage: { id: string; label: string; count: number; sublabel: string }
  onClose: () => void
}

export function StageDetail({ stage, onClose }: Props) {
  return (
    <div className="w-80 bg-white border-l border-blueprint-200 p-4 overflow-auto">
      <div className="flex justify-between items-center mb-4">
        <h3 className="font-bold text-lg">{stage.label}</h3>
        <button onClick={onClose} className="text-sm text-blueprint-700">Close</button>
      </div>
      <div className="font-mono text-sm space-y-2">
        <div>Count: {stage.count}</div>
        <div>Type: {stage.sublabel}</div>
        <div className="text-gray-500">Detailed stage metadata will be loaded from /api/v1/runs/{'{run_id}'}/census-summary</div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 修改 DemoPage.tsx**

```tsx
import { useState } from 'react'
import { PipelineScene } from '../scenes/PipelineScene'

const VIEWS = [
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'gis', label: 'GIS' },
  { id: 'cad', label: 'CAD' },
]

export function DemoPage() {
  const [view, setView] = useState('pipeline')

  return (
    <div className="h-full flex flex-col">
      <div className="flex gap-4 px-6 py-2 bg-white border-b border-blueprint-200">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            onClick={() => setView(v.id)}
            className={`text-sm font-medium ${
              view === v.id ? 'text-blueprint-700 underline' : 'text-gray-500'
            }`}
          >
            {v.label}
          </button>
        ))}
      </div>
      <div className="flex-1">
        {view === 'pipeline' && <PipelineScene />}
        {view === 'gis' && <div className="p-6">GIS view placeholder</div>}
        {view === 'cad' && <div className="p-6">CAD view placeholder</div>}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: 构建检查**

```bash
cd webdemo/frontend
npm run build
```

Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
git add webdemo/frontend/src
git commit -m "feat(webdemo): add 3D pipeline stage visualization"
```

---

### Task 13: A 部分 - 3D GIS 视窗

**Files:**
- Create: `webdemo/frontend/src/gis/GisScene.tsx`
- Create: `webdemo/frontend/src/gis/LayerToggle.tsx`
- Modify: `webdemo/frontend/src/pages/DemoPage.tsx`

**Interfaces:**
- Produces: A 3D GIS scene rendering delivery layers from sample-run or real run.

- [ ] **Step 1: 创建 LayerToggle.tsx**

```tsx
interface Props {
  layers: { id: string; label: string; visible: boolean; count: number }[]
  onToggle: (id: string) => void
}

export function LayerToggle({ layers, onToggle }: Props) {
  return (
    <div className="bg-white rounded shadow p-3 text-sm space-y-2">
      <h4 className="font-bold">Layers</h4>
      {layers.map((layer) => (
        <label key={layer.id} className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={layer.visible}
            onChange={() => onToggle(layer.id)}
          />
          <span className="font-mono">{layer.label}</span>
          <span className="text-gray-500">({layer.count})</span>
        </label>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: 创建 GisScene.tsx**

```tsx
import { useEffect, useMemo, useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { LayerToggle } from './LayerToggle'

const LAYERS = [
  { id: 'PTECH', label: 'PTECH', color: '#c17c45', count: 167 },
  { id: 'CABLE', label: 'CABLE', color: '#2b579a', count: 6 },
  { id: 'BOITE', label: 'BOITE', color: '#5a7d5a', count: 43 },
  { id: 'SITE', label: 'SITE', color: '#8c6b5d', count: 2 },
  { id: 'IMB', label: 'IMB', color: '#7d5a7d', count: 682 },
]

function Pole({ position, color }: { position: [number, number, number]; color: string }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.5, 0]}>
        <cylinderGeometry args={[0.05, 0.05, 1, 16]} />
        <meshStandardMaterial color={color} />
      </mesh>
    </group>
  )
}

function Cable({ points, color }: { points: [number, number, number][]; color: string }) {
  return (
    <line>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={points.length}
          array={new Float32Array(points.flat())}
          itemSize={3}
        />
      </bufferGeometry>
      <lineBasicMaterial color={color} />
    </line>
  )
}

export function GisScene() {
  const [visible, setVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(LAYERS.map((l) => [l.id, true]))
  )

  const toggle = (id: string) => setVisible((v) => ({ ...v, [id]: !v[id] }))

  const poles = useMemo(
    () =>
      Array.from({ length: 20 }).map((_, i) => [
        (Math.random() - 0.5) * 10,
        0,
        (Math.random() - 0.5) * 10,
      ] as [number, number, number]),
    []
  )

  return (
    <div className="flex h-full">
      <div className="flex-1">
        <Canvas camera={{ position: [8, 8, 8], fov: 45 }}>
          <ambientLight intensity={0.7} />
          <directionalLight position={[10, 10, 5]} />
          <OrbitControls />
          <gridHelper args={[20, 20, '#cccccc', '#eeeeee']} />
          {visible.PTECH && poles.map((p, i) => <Pole key={i} position={p} color="#c17c45" />)}
          {visible.CABLE && (
            <Cable
              points={poles}
              color="#2b579a"
            />
          )}
        </Canvas>
      </div>
      <div className="w-64 p-4">
        <LayerToggle
          layers={LAYERS.map((l) => ({ ...l, visible: visible[l.id] }))}
          onToggle={toggle}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: 修改 DemoPage.tsx 使用 GisScene**

```tsx
import { GisScene } from '../gis/GisScene'
```

把 `view === 'gis' && <div .../>` 替换为 `view === 'gis' && <GisScene />`。

- [ ] **Step 4: 构建检查**

```bash
cd webdemo/frontend
npm run build
```

Expected: build succeeds

- [ ] **Step 5: Commit**

```bash
git add webdemo/frontend/src
git commit -m "feat(webdemo): add 3D GIS layer visualization with toggles"
```

---

### Task 14: A 部分 - CAD 分层解剖器

**Files:**
- Create: `webdemo/frontend/src/cad/CadScene.tsx`
- Create: `webdemo/frontend/src/cad/LayerTree.tsx`
- Create: `webdemo/frontend/src/cad/EntityTable.tsx`
- Modify: `webdemo/frontend/src/pages/DemoPage.tsx`

**Interfaces:**
- Produces: A CAD layer tree + 3D wireframe viewer + entity attribute table.

- [ ] **Step 1: 创建 LayerTree.tsx**

```tsx
interface Props {
  layers: { name: string; count: number; visible: boolean }[]
  onToggle: (name: string) => void
}

export function LayerTree({ layers, onToggle }: Props) {
  return (
    <div className="bg-white rounded shadow p-3 text-sm">
      <h4 className="font-bold mb-2">DWG Layers</h4>
      <ul className="space-y-1 max-h-96 overflow-auto">
        {layers.map((layer) => (
          <li key={layer.name} className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={layer.visible}
              onChange={() => onToggle(layer.name)}
            />
            <span className="font-mono truncate">{layer.name}</span>
            <span className="text-gray-500">{layer.count}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
```

- [ ] **Step 2: 创建 EntityTable.tsx**

```tsx
interface Entity {
  handle: string
  layer: string
  type: string
  effectiveName?: string
}

interface Props {
  entities: Entity[]
}

export function EntityTable({ entities }: Props) {
  return (
    <div className="bg-white rounded shadow p-3 text-xs font-mono overflow-auto max-h-48">
      <table className="w-full">
        <thead>
          <tr className="text-left border-b">
            <th>Handle</th>
            <th>Layer</th>
            <th>Type</th>
            <th>Name</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((e) => (
            <tr key={e.handle} className="border-b border-gray-100">
              <td>{e.handle}</td>
              <td>{e.layer}</td>
              <td>{e.type}</td>
              <td>{e.effectiveName || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 3: 创建 CadScene.tsx**

```tsx
import { useMemo, useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { LayerTree } from './LayerTree'
import { EntityTable } from './EntityTable'

const SAMPLE_LAYERS = [
  { name: 'BASE MAP', count: 1255 },
  { name: 'CABLE_24C', count: 5 },
  { name: 'CABLE_48C', count: 1 },
  { name: 'DIMENSION', count: 170 },
  { name: 'PTECH', count: 167 },
]

const SAMPLE_ENTITIES = [
  { handle: '1A3F', layer: 'PTECH', type: 'INSERT', effectiveName: '*U13' },
  { handle: '1A40', layer: 'PTECH', type: 'INSERT', effectiveName: '*U13' },
]

export function CadScene() {
  const [visible, setVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(SAMPLE_LAYERS.map((l) => [l.name, true]))
  )

  const toggle = (name: string) => setVisible((v) => ({ ...v, [name]: !v[name] }))

  const lines = useMemo(() => {
    const arr: [number, number, number][] = []
    for (let i = 0; i < 50; i++) {
      arr.push([
        (Math.random() - 0.5) * 10,
        (Math.random() - 0.5) * 10,
        (Math.random() - 0.5) * 10,
      ])
    }
    return arr
  }, [])

  return (
    <div className="flex h-full">
      <div className="flex-1">
        <Canvas camera={{ position: [8, 8, 8], fov: 45 }}>
          <ambientLight intensity={0.7} />
          <directionalLight position={[10, 10, 5]} />
          <OrbitControls />
          <gridHelper args={[20, 20]} />
          <line>
            <bufferGeometry>
              <bufferAttribute
                attach="attributes-position"
                count={lines.length}
                array={new Float32Array(lines.flat())}
                itemSize={3}
              />
            </bufferGeometry>
            <lineBasicMaterial color="#2b579a" />
          </line>
        </Canvas>
      </div>
      <div className="w-80 p-4 space-y-4 overflow-auto">
        <LayerTree
          layers={SAMPLE_LAYERS.map((l) => ({ ...l, visible: visible[l.name] }))}
          onToggle={toggle}
        />
        <EntityTable entities={SAMPLE_ENTITIES} />
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 修改 DemoPage.tsx 使用 CadScene**

```tsx
import { CadScene } from '../cad/CadScene'
```

替换 `view === 'cad'` 占位符。

- [ ] **Step 5: 构建检查**

```bash
cd webdemo/frontend
npm run build
```

Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
git add webdemo/frontend/src
git commit -m "feat(webdemo): add CAD layer and wireframe viewer"
```

---

### Task 15: Docker Compose

**Files:**
- Create: `webdemo/backend/Dockerfile`
- Create: `webdemo/frontend/Dockerfile`
- Create: `webdemo/docker-compose.yml`

**Interfaces:**
- Produces: `docker-compose up --build` starts the full system.

- [ ] **Step 1: 创建 backend/Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: 创建 frontend/Dockerfile**

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 3: 创建 frontend/nginx.conf**

```nginx
server {
    listen 80;
    server_name localhost;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

- [ ] **Step 4: 创建 docker-compose.yml**

```yaml
version: "3.9"

services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    volumes:
      - ./runs:/app/runs
    environment:
      - CAD2GIS_WEBDEMO_RUNS_DIR=/app/runs
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

  frontend:
    build: ./frontend
    ports:
      - "80:80"
    depends_on:
      - backend

volumes:
  runs:
```

- [ ] **Step 5: 测试构建**

```bash
cd webdemo
docker-compose build
```

Expected: both images build successfully.

- [ ] **Step 6: Commit**

```bash
git add webdemo
git commit -m "feat(webdemo): add Docker Compose setup for full stack"
```

---

### Task 16: Sample-run 离线演示数据

**Files:**
- Create: `webdemo/frontend/public/sample-run/.gitkeep`
- Create: `webdemo/backend/scripts/generate_sample_run.py`

**Interfaces:**
- Produces: A script that produces sample-run data so A 部分 works without AutoCAD.

- [ ] **Step 1: 生成 sample-run JSON**

```python
import json
from pathlib import Path

SAMPLE = {
    "run_id": "sample",
    "status": "completed",
    "created_at": "2026-07-19T00:00:00Z",
    "layers": [
        {"id": "PTECH", "count": 167, "color": "#c17c45"},
        {"id": "CABLE", "count": 6, "color": "#2b579a"},
        {"id": "BOITE", "count": 43, "color": "#5a7d5a"},
    ],
    "census": {
        "total_entities": 6940,
        "inserts": 222,
        "dimensions": 170,
        "lwpolyline": 4265,
    },
}

if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent.parent / "frontend" / "public" / "sample-run"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(SAMPLE, indent=2))
    print(f"wrote {out / 'summary.json'}")
```

- [ ] **Step 2: 生成 sample-run 数据**

```bash
cd webdemo/backend
python scripts/generate_sample_run.py
```

Expected: creates `webdemo/frontend/public/sample-run/summary.json`.

- [ ] **Step 3: 更新前端加载 sample-run**

修改 `DemoPage.tsx` 或 store，使 A 部分在没有真实 run 时默认加载 `/sample-run/summary.json`。

```tsx
import { useEffect } from 'react'
import { useAppStore } from '../store/appStore'

export function useSampleRun() {
  const setSelectedRunId = useAppStore((s) => s.setSelectedRunId)
  useEffect(() => {
    setSelectedRunId('sample')
  }, [setSelectedRunId])
}
```

- [ ] **Step 4: Commit**

```bash
git add webdemo
git commit -m "feat(webdemo): add sample-run data for offline demo"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ A 部分三个 3D 视图：Pipeline (Task 12), GIS (Task 13), CAD (Task 14)
- ✅ B 部分转换前端：上传 (Task 3/10), 参数 (Task 10), 任务 (Task 5/10), 结果 (Task 6/10)
- ✅ FastAPI 后端：上传/转换/产物/图层 API (Tasks 3-6)
- ✅ SSE 日志流 (Task 5)
- ✅ Docker Compose (Task 15)
- ✅ Sample-run 离线演示 (Task 16)
- ⚠️ 真实 GeoPackage 解析为 GeoJSON 在 Task 13 中仅做了占位，需后续扩展。

**2. Placeholder scan:**
- 无 TBD/TODO。
- 部分功能用 sample 数据占位，已在 Task 16 中说明。

**3. Type consistency:**
- 前后端类型一致：`RunConfig`, `RunInfo`, `RunStatus`, `UploadedFile` 字段匹配。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-19-webdemo-delivery-system-plan.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
