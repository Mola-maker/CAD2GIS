# CAD2GIS FTTH 转换 Docker 镜像构建指南

- 日期: 2026-08-31
- 分支: `robustness` @ `2e0fd47`（十张 raw DWG 完美转换的微调分支）
- 目标: 构造一个在陌生机子上解压即用（`docker run` 挂载输出目录）的镜像，一键完成 10 张 APD DWG 的 `source/evidence/delivery.gpkg` 转换
- 转换模式: `--llm off`（全确定性，无需 API key，镜像自包含）

## 0. 已确认的事实与决策（勿改动）

| 项 | 值 |
|---|---|
| 基础运行时 | **方案 A**：micromamba/conda 环境（`env/environment.yml`，与已验证环境一字不差：GDAL 3.10 / PROJ 3.7 / Python 3.12） |
| LibreDWG | **源码编译**进镜像。源码: `/home/cat/dev/cpp/libredwg`（commit `f1f541c`，2026-07-04，SONAME `libredwg.so.0.0.14`，构建选项 `./configure --enable-python`，SWIG 绑定随 `make install` 安装到 `site-packages`） |
| census 期望 | **离线校准**：robustness 工作区 `baselines/<site>/config/` 已是正确字段值（例：lamteh_main `IMB=423` 为真值；846 是 main 分支代码未去附图噪声所致，**不是**期望值错误）。镜像构建前把校准产物固化进构建上下文 |
| 转换模式 | `--llm off` |
| raw 图纸 | 烧录进镜像（10 张 ~19MB）；输出目录运行时挂载 |
| Docker 桥接 | **WSL 集成已启用**：`/usr/bin/docker` 有效（指向 `/mnt/wsl/docker-desktop/cli-tools` 只读挂载，29.6.2），`docker info` 直通 daemon；`docker.exe` 亦可直调（见 §1） |
| AutoCAD reader | 镜像**不含** AutoCAD reader 能力（`autocad.py` 代码随 `src/` 进镜像，但 accoreconsole 为 Windows 原生程序，Linux 容器内不可运行）；补偿措施见 §9 |

## 1. 前置：确认 Docker 与 WSL2 桥接

**现状（2026-08-31 实测）：WSL 集成已启用，直接可用。**

```bash
docker --version          # Docker version 29.6.2（/usr/bin/docker 有效，指向 /mnt/wsl/docker-desktop/cli-tools）
docker info --format '{{.ServerVersion}}'   # 29.6.2，直通 Windows 侧 daemon
docker images             # 能列出镜像即桥接可用
```

> 历史说明：Docker Desktop 未运行时 `/usr/bin/docker` 会短暂表现为死链（`/mnt/wsl/docker-desktop` 未挂载），启动 Docker Desktop 后自动恢复，无需配置。

若引擎未启动：Windows 侧打开 Docker Desktop，或
```powershell
# Windows PowerShell
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
```
等待 `docker info` 可响应后再继续。若希望使用 `docker.exe` 直调（不经 WSL 集成）：`/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe` 同样可用。

## 2. 镜像素材清单（构建上下文）

| 素材 | 来源 | 进入镜像的方式 |
|---|---|---|
| robustness 代码 | 本工作区（`src/`、`pyproject.toml`、`env/environment.yml`） | `COPY` |
| libredwg 源码 | `/home/cat/dev/cpp/libredwg` | `COPY`（或构建前打 tar 放入上下文） |
| 10 站校准配置 | `baselines/<site>/config/`（10 站）+ `baselines/<site>/review/`（如存在） | `COPY` |
| 10 张 DWG | `raw/*.dwg` | `COPY` |
| 批量转换脚本 | `scripts/regenerate_runs.py`（参考其命令构造） | `COPY` |

> `baselines/*/config/*.bak.2345`、`.omc/`、`experiment/`、`official/`、webdemo 组件不进入镜像。

## 3. 镜像结构（七层，按缓存友好顺序）

```text
1. base           micromamba/conda 基础镜像
2. gis-runtime    conda env create（environment.yml：GDAL 3.10 / PROJ 3.7 / ezdxf / shapely / pytest / ruff）
3. libredwg       源码编译 → /usr/local（库 + dwg* CLI + SWIG Python 绑定）   ← 慢层，单独缓存
4. code           COPY src/ + pyproject.toml → pip install（非 editable）
5. config         COPY baselines/（10 站校准产物）
6. data           COPY raw/（10 张 DWG）
7. entrypoint     run_all.sh（PROJ_DATA 注入 + doctor 预检 + 10 站 convert --llm off → /out/）
```

## 4. Dockerfile 草案

```dockerfile
# ── 1. base ──────────────────────────────────────────────────────────────
FROM mambaorg/micromamba:2.0.5 AS gis
USER root
ENV MAMBA_ROOT_PREFIX=/opt/mamba

# ── 2. gis-runtime ───────────────────────────────────────────────────────
COPY env/environment.yml /app/env/environment.yml
RUN micromamba create -y -n cad2gis -f /app/env/environment.yml \
    && micromamba clean -ay

# ── 3. libredwg（源码编译 + SWIG python 绑定）────────────────────────────
# 构建依赖一次性安装，避免残留在最终层
RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf automake libtool gcc g++ make swig python3-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
COPY libredwg/ /usr/src/libredwg/
WORKDIR /usr/src/libredwg
RUN autoreconf -fi \
    && ./configure --prefix=/usr/local --enable-python \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig

# ── 4. code ──────────────────────────────────────────────────────────────
COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
RUN /opt/mamba/envs/cad2gis/bin/pip install /app \
    && /opt/mamba/envs/cad2gis/bin/python -c "import LibreDWG; print('LibreDWG OK')"

# ── 5. config + 6. data ──────────────────────────────────────────────────
COPY baselines/ /app/baselines/
COPY raw/ /app/raw/

# ── 7. entrypoint ────────────────────────────────────────────────────────
COPY scripts/run_all.sh /usr/local/bin/run_all.sh
RUN chmod +x /usr/local/bin/run_all.sh

ENV CAD2GIS_READER_BACKEND=libredwg
ENV PATH=/opt/mamba/envs/cad2gis/bin:$PATH
ENV PROJ_DATA=/opt/mamba/envs/cad2gis/share/proj
ENV GDAL_DATA=/opt/mamba/envs/cad2gis/share/gdal
ENV GDAL_DRIVER_PATH=/opt/mamba/envs/cad2gis/lib/gdalplugins
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/run_all.sh"]
```

> 注意：conda 环境的 activate.d 在 `docker run` 下不会自动执行，`PROJ_DATA`/`GDAL_DATA` 必须由 ENV 或 entrypoint 显式注入（否则报 `Invalid source CRS`——Linux 验证中实测的坑）。

## 5. 离线校准（构建前完成，一次性）

`baselines/<site>/config/` 已是正确校准值（用户确认）。构建前执行以下校验，确保 10 站配置与当前源码一致：

```bash
# 1) 每站 validate（需先有 review/source_inventory.json；缺失时先 bootstrap 生成）
for site in hutabohu lamteh_main lamteh_sf kletek semarang_sf darat_sekip_sf \
            manado-tomohon_uplink taipa tinggar tinggede; do
  cad2gis validate --project "baselines/$site" --json | grep -o '"valid": [a-z]*'
done

# 2) 任一站点失败或 census 期望与事实不符时，重跑 AI 校准（需 DEEPSEEK_API_KEY）
export DEEPSEEK_API_KEY=...   # 只进环境，不落盘
python scripts/auto_convert_runs.py --dry-run   # 先预览
python scripts/auto_convert_runs.py             # 全 10 站（自动备份旧配置到 scripts/logs/backups/）
```

校准产物固化进镜像构建上下文：`baselines/<site>/config/*.json`（source_profile / mapping_registry / spatial_regions）+ `review/`。

> 6 张验证集（semarang_sf 等）要求 source-bound 处理，不得复用基线门禁；校准后各站 config 各自独立。

## 6. 入口脚本 `scripts/run_all.sh`（需新建）

```bash
#!/usr/bin/env bash
set -euo pipefail

# PROJ/GDAL 数据路径（docker run 无 activate.d）
export PROJ_DATA="${PROJ_DATA:-/opt/mamba/envs/cad2gis/share/proj}"
export GDAL_DATA="${GDAL_DATA:-/opt/mamba/envs/cad2gis/share/gdal}"
export CAD2GIS_READER_BACKEND=libredwg
export CAD2GIS_FULL_DWG_TESTS=0

OUT="${OUT:-/out}"
mkdir -p "$OUT"

# 预检
cad2gis doctor --deep --strict --json | grep -q '"status": "ready"' || { echo "doctor not ready"; exit 1; }

# 10 站转换（--llm off 全确定性；run-dir 输出到 /out/<site>）
# 站点-图纸映射已通过 source_binding.sha256 与 raw/ 文件逐一核验（2026-08-31）
declare -A SRC=(
  [hutabohu]="raw/APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg"
  [lamteh_main]="raw/APD - KELURAHAN LAMTEH DAYAH ACEH.dwg"
  [lamteh_sf]="raw/APD - KELURAHAN LAMTEH DAYAH ACEH - SF.dwg"
  [kletek]="raw/APD - KLETEK RW 05 SIDOARJO.dwg"
  [semarang_sf]="raw/APD - BULU LOR RW 05 SEMARANG - SF.dwg"
  [darat_sekip_sf]="raw/APD - DARAT SEKIP RW 12 PONTIANAK - SF.dwg"
  [manado-tomohon_uplink]="raw/APD - MANADO- UPLINK_FWA_OLT_TOMOHON_TO_EMR- 46478_FO_24C.dwg"
  [tinggede]="raw/APD - PERUMAHAN TINGGEDE VIEW PALU.dwg"
  [taipa]="raw/APD - TAIPA RW 05 PALU.dwg"
  [tinggar]="raw/APD - TINGGAR RW 04 SERANG.dwg"
)
for site in hutabohu lamteh_main lamteh_sf kletek semarang_sf darat_sekip_sf \
            manado-tomohon_uplink taipa tinggar tinggede; do
  echo "== convert: $site =="
  cad2gis convert "${SRC[$site]}" \
    --project "baselines/$site" \
    --run-dir "$OUT/$site" \
    --llm off --json >/dev/null
done

echo "ALL 10 SITES CONVERTED -> $OUT"
```

## 7. 构建与冒烟验证

```bash
# 构建（libredwg 编译层约 5-10 分钟，缓存后秒级）
cd <构建上下文目录>
docker.exe build -t cad2gis-ftth:0.1.0 .

# 冒烟：doctor + 单站转换
docker.exe run --rm cad2gis-ftth:0.1.0 bash -c 'cad2gis doctor --deep --strict --json'
mkdir -p /tmp/cad2gis_out
docker.exe run --rm -v /tmp/cad2gis_out:/out cad2gis-ftth:0.1.0   # 全 10 站

# 验收标准（对标 Linux 验证基线）
ls /tmp/cad2gis_out/hutabohu/delivery.gpkg          # 存在
ls /tmp/cad2gis_out/lamteh_main/delivery.gpkg       # 存在
python3 -c "
from osgeo import ogr
for site in ['hutabohu','lamteh_main']:
    ds = ogr.Open(f'/tmp/cad2gis_out/{site}/delivery.gpkg')
    counts = {ds.GetLayerByIndex(i).GetName(): ds.GetLayerByIndex(i).GetFeatureCount()
              for i in range(ds.GetLayerCount())}
    print(site, counts)
"
```

验收对照（robustness 校准后的 census 期望，取自 `baselines/<site>/config/source_profile.json`）：
- lamteh_main: BOITE 38 / CABLE 53 / IMB 423 / PTECH 208 / SITE 2 / ZPM 38 / ZNRO 1
- kletek: BOITE 9 / CABLE 9 / IMB 167 / PTECH 33 / SITE 1 / ZPM 9 / ZNRO 2
- hutabohu: IMB 682（以工作区 config 为准）

## 8. 陌生机子使用方式

```bash
# 导出镜像或从 registry 拉取后：
mkdir -p $HOME/cad2gis_out
docker run --rm -v $HOME/cad2gis_out:/out cad2gis-ftth:0.1.0
# 产物: $HOME/cad2gis_out/<site>/{source,evidence,delivery}.gpkg + run_manifest.json + qgis/
```

## 9. AutoCAD reader 补偿措施（镜像不含 Windows 原生 reader）

**现状**：镜像基于 robustness 分支，代码层包含 `src/cad2gis/reader/autocad.py`，但其调用 accoreconsole（AutoCAD Core Console）是 Windows 原生程序，Linux 容器内**不可运行**。镜像只启用 `CAD2GIS_READER_BACKEND=libredwg`。

**补偿措施**：

1. **输出契约同构**：两种 reader 产出同一 `DWGRecordInventory` 协议（`contracts.py`）→ 同一 canonical 流水线 → 同一 `source/evidence/delivery.gpkg` 结构与 manifest schema。镜像产物与 Windows autocad 路径产物可直接互换核验，不存在格式差异。

2. **读数已对齐**：robustness 的源码微调（去附图噪声等）使 LibreDWG 读数与 autocad 一致（例：lamteh `IMB=423` 两 reader 相同）。镜像内转换与 autocad 路径的 census 期望共用同一套 `baselines/` 校准值。

3. **Windows 原生 autocad 路径并存**：同一 `baselines/<site>/config` 可在 Windows 机本地安装（README 安装流程，`$env:CAD2GIS_READER_BACKEND="autocad"`）跑出同构产物。两路径共用校准配置，不重复校准。

4. **新图/未校准图工作流**：新 DWG 若有 autocad 环境，先在 Windows 侧 `auto-convert` 校准（生成 reviewed config），校准产物可直接拷入镜像基线（或下一版镜像重建时固化）；无 autocad 环境则镜像内 LibreDWG 校准亦可（同一 `auto_convert_runs.py`，`--llm assist`）。

5. **fail-closed 保障**：镜像内 reader 不可用时 `doctor`/`convert` 明确失败（`ReaderUnavailableError`），不会输出伪成功 GeoPackage——镜像产物总是可追溯的（`run_manifest.json` 记录 `extraction_backend=libredwg`）。

## 10. 故障排查

| 现象 | 原因 | 处置 |
|---|---|---|
| `Invalid source CRS: 'EPSG:3857'` | PROJ_DATA 未注入（docker run 不执行 activate.d） | ENV/entrypoint 显式设置（§4/§6） |
| `LibreDWG ... ReaderUnavailable` | SWIG 绑定未装进 python 环境 | 检查 `python -c "import LibreDWG"`；绑定在 `/usr/local/lib/python3.12/site-packages/` |
| `census mismatch: expected X got Y` | 期望与当前 reader 读数不符 | 重跑离线校准（§5）；确认用的是 robustness 源码（main 分支会多出附图噪声） |
| `Mapping registry is draft` | config review 状态非 accepted | 用 `baselines/<site>/config` 完整产物（含 review 字段），或重跑 auto-convert |
| 慢 | libredwg 编译层每次重编 | 保持层缓存；源码目录稳定后打独立层 |

## 11. 相关参考

- Linux 验证报告与部署文档改进: `CAD2GIS_validation/reports/`（`LINUX_VALIDATION_REPORT.md`、`DEPLOYMENT_DOC_IMPROVEMENTS.md`、`RELEASE_PACKAGE_MANIFEST.md`）
- libredwg 源码: `/home/cat/dev/cpp/libredwg`（本地 git 树，含已构建产物与 SWIG 绑定）
- robustness 批量校准脚本: `scripts/auto_convert_runs.py`、`scripts/regenerate_runs.py`
- 分支开发说明: `.omc/plans/ralplan-robustness-branch-dev-reader.md`
