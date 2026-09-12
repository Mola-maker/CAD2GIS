# GIS → CAD 全量升级验证记录

日期：2026-09-12 UTC。实现基于本次 `git pull --ff-only` 后确认的
`1d4086213f8bb882d8ab6cddd64cb1cdf434a6e9`，独立工作树
`E:/branch_CAD2GIS/CAD2GIS-reverse`，分支 `codex/gis-to-cad-20260912`。
原开发工作树保留。本记录描述该子分支的验证，不代表合并到 main 或发布正式版本。
以下本地构建与回归在提交前完成，收据中的未提交状态是验证当时的历史状态。

本文替代第一版 153 项测试和旧 wheel 的记录。证据根目录统一为
`E:/branch_CAD2GIS/validation/reverse-full-20260912`。
最终交付收据为该目录的 `final-delivery-receipt.json`。

## 已交付能力

| 路径 | 行为 | 边界 |
| --- | --- | --- |
| GIS → DXF/DWG | GeoPackage/GeoJSON 检查、投影转换、孔洞/多部件/XYZ、标签、属性、源几何核验 | 保留 GIS 编辑；不猜测原块、标注、曲线和布局 |
| 源绑定坐标逆变换 | 匹配 forward manifest、交付哈希、partition、单位及实际变换管线 | 恢复记录的坐标框架，不撤销 GIS 几何编辑或证明绝对精度 |
| 源快照图元复现 | 从完整事实生成 LINE、LWPOLYLINE、CIRCLE、ARC、ELLIPSE 并核验 | 与 GIS 模式分开，GIS_edits_applied=false；拒绝 BLOCKDEF 坐标系 |
| CLI / MCP / batch | 两类 inspect/convert/verify；GIS 批量计划预检和 1–8 个工作线程 | 真实 DWG 并发验证范围为 2；失败现场保留并明确报错 |

输出均为新目录。DXF 为 R2018；DWG 使用已安装且实测的 AutoCAD Core Console。
两个 verify 命令都会对当前 DWG 重新原生回读，不仅依赖保留 DXF、计数或重封的哈希。
使用方法见 [GIS_TO_CAD.md](GIS_TO_CAD.md)。

## 当前代码回归

环境：Python 3.12.13、pytest 9.1.1、ezdxf 1.4.4、Shapely 2.1.2、pyproj 3.8.0。
GDAL 通过项目现有 `ensure_osgeo_runtime()` / pyramids-gis 加载。
进程局部设置 `OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`，未修改全局运行时或页文件。

完整套件收集 **1657 项**，首次结果为 **1643 passed、12 skipped、2 setup errors**，
4 warnings。两项错误均为既有 semantic transaction 测试的夹具读文件分配出现
`MemoryError`，发生于完整测试与两个 AutoCAD 进程同时执行期间。原生任务退出后，
原代码下单独重跑这两项：**2 passed in 1.56s**。

合计 **1645 项通过、12 项跳过，无未解决失败**。这不是声称一次完整运行即全绿。
运行前后 src、tests 和 pyproject 哈希一致。完整运行 292.40 秒；
`full-suite-delivery/run_suite.py` 保存 collection、代码哈希、JUnit、错误及进程退出状态。
证据：`full-suite-delivery/suite_summary.json`、`junit.xml`、`failures.json`、
`skip-reasons.json`、`full-suite-delivery-retry.xml`。
Ruff 对逆向模块/测试及修改的集成模块检查通过；`git diff --check` 通过。

12 项跳过：复杂原 DWG opt-in 2 项；Windows 符号链接权限 3 项；未配置 Semarang
属性回归原 DWG 1 项；当前工作树缺少可选 Hutabohu 原始证据基线 4 项；POSIX 文件
执行权限 2 项。标准套件保持 `CAD2GIS_FULL_DWG_TESTS=0`，真实逆向 DWG 另行验证。

## 独立几何核验与真实语料

输入为 `CAD2GIS-multipage/baselines/*/delivery/delivery.gpkg`，保留各项目投影 CRS，
标签字段为 `display_label`。独立审查脚本直接解释 SQLite/WKB 比较 CAD，不复用
生产读取器/写入器作为答案。已检查 **4814 个要素、8449 个 CAD 实体、3635 个标签、
15492 个顶点、209575 个属性值**；GIS→DXF 最大观测坐标误差为 0。

| 项目 | GIS 要素 | CAD 实体（含标签） | 标签 |
| --- | ---: | ---: | ---: |
| darat_sekip_sf | 28 | 43 | 15 |
| hutabohu | 1150 | 2043 | 893 |
| kletek | 263 | 473 | 210 |
| lamteh_main | 1000 | 1671 | 671 |
| lamteh_sf | 56 | 84 | 28 |
| manado-tomohon_uplink | 375 | 677 | 302 |
| semarang_sf | 39 | 59 | 20 |
| taipa | 673 | 1181 | 508 |
| tinggar | 715 | 1292 | 577 |
| tinggede | 515 | 926 | 411 |
| 合计 | 4814 | 8449 | 3635 |

证据：`corpus-verification.json`、`corpus-batch/`、
`fidelity-audit/fidelity-report.json` 和 `FINDINGS.md`。
结构补充样例明确为合成数据：10 个要素、20 个几何实体、52 个顶点、
23 个 XYZ 顶点、3 个孔洞。重封哈希但保持实体数不变的几何、标签、属性、
单位、样式和句柄篡改均有拒绝用例。

## 原生 DWG 与源 CAD 事实

原生程序为 AutoCAD 2027 Core Console，版本 `X.118.0.0`，可执行文件 SHA-256：
`44d10af0c32fb54c52b3a34751a8e9b7c860448d02089a0f09af68649f72b3e2`。
程序隐藏运行，仅处理暂存副本。LibreDWG 0.14 的 DWG writer 实验未通过，不作为生产回退。

ASCII DXFOUT 在 Manado 出现 `1.117587e-8` 误差；实际二进制 DXFOUT 将全语料最大
误差降为 **3.725290298461914e-9**，仍使用原有 **1e-8** 门槛。
早期资格证据位于 `dwg-qualification/canonical/final-native-corpus-summary.json`；
最终包已重新双线程转换全部 10 个项目，每项完成写出、保留回读及新原生回读，
结果位于 `package-smoke-final-03/dwg-corpus/`。

原始 Kletek DWG：`APD_test/KLETEK RW 05 SIDOARJO.dwg`，308753 字节，SHA-256：
`756f6e5bb4feb61fed2d86046478e0fb714434628bd0f17db4d78fef0b221016`。
当前 AutoCAD 快照有 1520 条记录，跨 Model、纸空间和块定义；664 条完整曲线事实
不等于复现了 664 个图元。实际选择并核验 **252 个 Model 图元**：
248 LWPOLYLINE、2 ARC、2 LINE。CIRCLE/ELLIPSE 的完整参数路径由合成测试覆盖。
最终安装包的源模式也已重新生成 DWG 并通过新原生回读：
`package-smoke-final-03/source-verification.json`。

GIS 中存在源曲线拆分和支撑点移位，不能只凭实体 ID 将原曲线贴回交付结果。
源快照模式不应用 GIS 编辑；自定义线型、块实例、布局、字体、标注和未保留样式
没有被声称为恢复。完整调查见 `source-fidelity/SOURCE_FIDELITY.md`。

原生坐标逆变换以一个 canonical **合成 forward run** 验证，30 个闭合点正逆误差均为 0。
最终安装包已重跑该路径。源 CAD SHA 为 manifest 声明值，保留
`source_cad_bytes_verified=false`；OSM 权威仍为 `relative_osm`。
证据：`native-registered/manifest.json`、
`package-smoke-final-03/registered-native/manifest.json`。
未证明所有实际项目均可恢复原生坐标或达到测量精度。

## 最终安装包

wheel：`dist-final/cad2gis-0.6.0-py3-none-any.whl`，SHA-256：
`523d506b392ac2beb149c13c0ccc47ca199a4973cf2258c4b6682a61bd9198dc`。

使用 setuptools 84.0.0 / build 1.6.0 无隔离构建，uv 以 `--no-index --no-deps`
离线安装到 `installed-final`。125 个打包 Python 源文件逐字节匹配工作树及安装目录，
16 个关键模块确认从安装目录导入，7 个 CLI 入口及 6 个逆向 MCP 工具注册通过。
同一已安装包完成全语料 DWG、Kletek CABLE DXF、源绑定坐标逆变换和 252 个源图元
DWG 的验证。最终包验收 **passed，79.0 秒**，输入文件哈希未变。
证据：`package-smoke-final-03/report.json` 及保存的脚本 `script.py`。

复用现有依赖，未验证全新机器从零安装。版本仍为 0.6.0，须用上列哈希识别本地构建。
安装说明已区分该包和不含本次未发布功能的远程 main；未替换用户已有 MCP 运行时。

首次 `package-smoke-final-02` 与全量测试重叠时 6 个项目通过、4 个失败：
3 个 AutoCAD 子进程退出 1、1 个 Python MemoryError。原失败报告和现场保留，
未覆盖或标成成功；随后无代码变更、与全量测试分开运行的双线程包验收全部通过。
另一次最初启动因构建目录 ACL 无法读 wheel，恢复仅本次生成目录的继承权限后继续。

## 审查与视觉范围

- `.omo/evidence/reverse-full-upgrade-code-review.md`：GIS、坐标及工具链审查。
- `reverse-dwg-final-independent-review.md`：文件描述符绑定、解析缓冲区、独占发布、
  子进程退出和失败保留 APPROVE；独立重跑 47 项通过。
- `reverse-source-final-audit.md`：根代理集成审查及源记录/句柄/样式修复，50 项
  source/MCP 测试通过；不冒充修复作者之外的独立批准。
- `reverse-delivery-doc-review.md`：能力边界与证据映射 PASS。

`visual-evidence/` 保留真实 Kletek 中间 DXF/原生回读、孔洞细节和源图元的 SVG/PNG，
输入哈希绑定于 `render-report.json`；Kletek 两张单色预览像素一致。
这些是 ezdxf 渲染检查，未进行 AutoCAD GUI、字体、打印或制图交付验收。
所有模式保留 `original_cad_restored=false`、`absolute_accuracy_verified=false`。
