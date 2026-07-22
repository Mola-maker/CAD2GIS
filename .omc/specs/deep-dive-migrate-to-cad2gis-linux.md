# Spec: 迁移至 CAD2GIS_Linux 分支

## Goal
从 main 分支提取 `experiment/` 和 `.omc/` 知识库，创建独立可移植的 `CAD2GIS_Linux` 分支，解耦全部 WSL2 绝对路径依赖和 external 目录依赖。

## Constraints
1. 不迁移 DWG 文件（gitignore）、output 产物、__pycache__、.bak 文件
2. 不迁移 `py_scripts/`（已被 `python/` 替代）
3. 不迁移 `converter_orig.py`（备份文件）
4. 不迁移 `.omc/state/`（运行时可重建）
5. 不迁移 experiment/ 子目录下的 `.omc/state/`（会话残留）
6. 新分支不保留 main 的任何提交历史，以孤立分支（orphan）形式创建

## Acceptance Criteria

| ID | Criterion | Verification |
|----|-----------|-------------|
| AC-1 | `CAD2GIS_Linux` 分支存在且为 orphan 分支 | `git log --oneline` 只有初始提交 |
| AC-2 | `python/` 全部文件就位，无 WSL2 绝对路径 | `grep -r "/usr/local" python/` 返回空 |
| AC-3 | `config/` 包含 hutabohu.json + aceh_main.json + aceh_sf.json + legend_exclusions.json | `ls config/` |
| AC-4 | `evaluation_standards/APD/` 包含 8 个 CSV | `ls evaluation_standards/APD/*.csv | wc -l` = 8 |
| AC-5 | `.omc/specs/` + `.omc/plans/` + `.omc/wiki/` 完整迁移 | 与 main 分支文件数一致 |
| AC-6 | `.gitignore` 排除 DWG/GPKG/QGZ/output/dump.json/__pycache__/*.pyc/.bak | 检查规则覆盖 |
| AC-7 | `README.md` 包含安装说明 | 包含 GDAL + LibreDWG 安装步骤 + 环境变量说明 |
| AC-8 | `python/cad_common.py` 使用便携 LibreDWG 搜索 | `_find_libredwg()` 多路径搜索 |
| AC-9 | `python/ftth_converter.py` 使用便携 SWIG 搜索 | `LIBREDWG_PYTHON` + site-packages 搜索 |
| AC-10 | `python/domain_vocab.py` 无外部文件依赖 | 无 `official/Shape` 引用 |

## Non-Goals
- 不修复 TEXT 实体文本提取 Bug（已记录为已知问题）
- 不生成 GPKG 产物
- 不在此分支运行转换验证
- 不修改 py_scripts/ 原始脚本

## Technical Context

### 迁移源 → 目标映射

```
main:experiment/python/*           → CAD2GIS_Linux:python/*
main:experiment/config/*           → CAD2GIS_Linux:config/*
main:experiment/evaluation_standards/ → CAD2GIS_Linux:evaluation_standards/APD/  （重组）
main:experiment/archives/*         → CAD2GIS_Linux:archives/*
main:experiment/guide/*            → CAD2GIS_Linux:guide/*
main:.omc/specs/*                  → CAD2GIS_Linux:.omc/specs/*
main:.omc/plans/*                  → CAD2GIS_Linux:.omc/plans/*
main:.omc/wiki/*                   → CAD2GIS_Linux:.omc/wiki/*
main:.omc/drafts/*                 → CAD2GIS_Linux:.omc/drafts/*
main:.omc/notepad.md               → CAD2GIS_Linux:.omc/notepad.md
main:.omc/.gitignore               → CAD2GIS_Linux:.omc/.gitignore
```

### 排除清单

| 路径 | 原因 |
|------|------|
| `experiment/py_scripts/` | 已被 python/ 替代 |
| `experiment/output/` | 运行时产物 |
| `experiment/*.dwg` | 输入文件 |
| `experiment/*.gpkg` | 产物 |
| `experiment/*.qgz` | 产物 |
| `experiment/*_dump.json` | 缓存文件 |
| `experiment/**/__pycache__/` | Python 缓存 |
| `experiment/**/*.pyc` | 编译缓存 |
| `experiment/**/*.bak` | 备份文件 |
| `experiment/python/converter_orig.py` | 备份 |
| `.omc/state/` | 运行时状态 |
| `.omc/sessions/` | 会话残留 |
| `experiment/**/.omc/` | 子目录会话残留 |

### evaluation_standards 重组

```
evaluation_standards/APD/          ← As Plan Drawing（印尼 FTTH 规范，暂用摩洛哥标准）
├── BOITE.csv
├── CABLE.csv
├── INFRASTRUCTURE.csv
├── PTECH.csv
├── SITE.csv
├── ZNRO.csv
├── ZPM.csv
└── VERIFICATION_RULE.csv
```

### .gitignore 内容

```
*.dwg
*.gpkg
*.qgz
*_dump.json
output/
__pycache__/
*.pyc
*.bak
converter_orig.py
.omc/state/
```

### README.md 关键内容

```
# CAD2GIS Linux — Portable FTTH DWG-to-GeoPackage Converter

## 安装

### GDAL
pip install gdal

### LibreDWG
git clone https://git.savannah.gnu.org/git/libredwg.git
cd libredwg && ./autogen.sh && ./configure && make && sudo make install

# 或本地构建（无需 root）：
./configure --prefix=$HOME/.local && make && make install
export LIBREDWG_SO=$HOME/.local/lib/libredwg.so

## 环境变量

LIBREDWG_SO      libredwg.so 路径（默认搜索 /usr/local/lib, /usr/lib, ./）
LIBREDWG_PYTHON   LibreDWG SWIG 绑定的父目录（默认搜索 site-packages）
DWGREAD_BIN       dwgread CLI 路径（layout_miner JSON 导出用，可选）

## 使用

python3 -m python.ftth_converter \
  --input <file.dwg> \
  --output <output.gpkg> \
  --config config/<project>.json \
  --source-crs EPSG:3857 --target-crs EPSG:3857

## 项目配置

config/ 目录包含项目 JSON 配置文件：
- hutabohu.json   — APD DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO
- aceh_main.json  — APD KELURAHAN LAMTEH DAYAH ACEH（主项目）
- aceh_sf.json    — APD KELURAHAN LAMTEH DAYAH ACEH（SF 独立项目）
```

## Ontology

| 实体 | 路径 | 描述 |
|------|------|------|
| 转换器包 | `python/` | 15 个模块，已全部移植化（无 WSL2 绝对路径） |
| 项目配置 | `config/` | 3 个项目 JSON + 图例排除 |
| 验收标准 | `evaluation_standards/APD/` | 8 个 CSV，APD 工程类型共用 |
| 文档 | `archives/` + `guide/` | 聚合报告 + GeoFormer 提示词 + 验证循环 |
| 知识库 | `.omc/` | specs + plans + wiki + drafts |
| 安装说明 | `README.md` | GDAL + LibreDWG 安装 + 环境变量 |
