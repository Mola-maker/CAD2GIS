# CAD2GIS 0.6.0 升级与使用

这版整合了三份上传代码包，以 `codex-osm-precision` 的 `e401383` 为起点。
`main` 为 `e820c4c`，实图验证分支为 `747d666`；后者的修改已包含在 OSM 包中。
原始 DWG、历史 baseline、已有审核记录均保留。

## 安装

交付中的 Linux x86_64 安装包包含 Python、GDAL/PROJ、LibreDWG、Agent 和 review
依赖。构建环境使用 glibc 2.39；该包要求 glibc 2.39 或更新版本。

Debian/Ubuntu 系统在下载目录执行：

```bash
sudo apt install ./cad2gis_0.6.0_amd64.deb
cad2gis doctor --deep --strict --profile full --json
cad2gis-desktop
```

不做系统安装时，解压 portable archive：

```bash
tar -xzf cad2gis-0.6.0-linux-x86_64.tar.gz
./cad2gis-0.6.0/bin/cad2gis doctor --deep --strict --profile full --json
./cad2gis-0.6.0/bin/cad2gis-desktop
```

wheel 是 Python 软件包，未打包全部 native runtime。需要从源码运行时，使用
Python 3.11/3.12，在源码根目录执行 `python -m pip install ".[agent]"`，再运行
`cad2gis runtime install` 和 strict doctor。Windows/Android 的版本来源与共用 UI
已更新；是否存在经过本轮实际构建和运行的安装包，须看随附验证报告，不能由代码
修改推断平台已经验收。

## 第一次使用

1. 打开 Desktop，选择 DWG 项目；首页把导入、原图检查、位置校正和导出放在同一条任务路径。
2. 先检查原图、文字、曲线与实体保留情况，再检查 GIS 位置。缺少项目配置时使用
   source inspection/onboarding 建立新的绑定；不要复制另一张图的业务数量或审核状态。
3. 在地图中打开 OSM roads、CAD routes、连接节点和残差层，区分绘图形状、转换位置与参考道路。
4. 添加分布充分的控制点，另留独立检查点。选中控制点后可以按 `0.01 / 0.1 / 1 m`
   向东、西、南、北微调，并撤销或恢复最初选点。
5. 检查点用于验收，不参与训练点拟合。保存控制点后先看预览与残差，再生成新的 GIS run。

装饰图由 GPT Image 生成，已用于 Desktop 和共用 review UI；地图、连接关系、精度指标
与残差仍从真实矢量和计算结果绘制。

```bash
cad2gis convert drawing.dwg --project project --run-dir new-run --json
cad2gis review new-run --workspace review-workspace --port 8765
```

随后打开 `http://127.0.0.1:8765/`。在线 OSM 底图由用户显式打开。

## 主要修复

| 链路 | 0.6.0 的变化 |
|---|---|
| DXF/OCS → WCS | 转换镜像、elevation 和插入点坐标，保留真实 Z、法线和原始 bulge。 |
| 源曲线 → 显示 | 有界细分圆弧，保留原始顶点和解析长度；不完整曲线保留为带原因的 metadata。 |
| OSM 匹配 | 法向信息矩阵排除直线/平行线退化；Huber point-to-line refinement；重复路网候选不再错误合并。 |
| 中间证据 | 五个可回放矢量层、真实 OSM node IDs、桥隧/layer 属性、来源与 revision 哈希。 |
| GCP 工作台 | 地面距离微调、撤销/复原、持久历史、并发 revision 检查，训练/检查残差分开。 |
| Agent | MCP 错误进入纠错预算，保留 structuredContent，限制失败尝试和未知结果的重复写入。 |
| Runtime / 发布 | 修复归档丢失执行权限、统一 0.6.0 版本，补充离线回归与平台 CI。 |

实现、边界、测试和官方文档依据分别记录在
[几何修复](UPGRADE_GEOMETRY.md)、[OSM](UPGRADE_OSM.md)、[GCP](UPGRADE_GCP.md)、
[Agent/CI](UPGRADE_AGENT_CI.md)、[界面](UPGRADE_UI.md) 和
[架构复核](UPGRADE_ARCHITECTURE_REVIEW.md) 中。性能测量分别见
[源 GeoPackage 写入](UPGRADE_SOURCE_WRITE_PERFORMANCE.md) 与
[完整阶段哈希](UPGRADE_STAGE_HASH_PERFORMANCE.md)；加速比仅代表所测阶段。

## 九份 DWG 与旧配置

本轮九份原始 DWG 的哈希均与历史 source binding 相符；导出的 **152,621 条 reader
records** 全部进入 source GeoPackage，九份数量守恒和 SQLite integrity check 均通过。
这表示读器返回记录被完整保存，并不等于已经证明所有 CAD 实体都可精确绘制或业务语义全部正确。

| 图纸 | 保存的 reader records | 原配置兼容处理 |
|---|---:|---|
| Lamteh SF | 59,369 | 原绑定一致。 |
| Lamteh main | 63,483 | 原绑定一致。 |
| Manado–Tomohon uplink | 5,057 | named paper 历史契约未迁移，保留阻挡。 |
| Tinggar | 11,141 | named paper 历史契约未迁移，保留阻挡。 |
| Kletek | 1,538 | 使用同字节的历史文件名副本。 |
| Semarang SF | 1,455 | named paper 历史契约未迁移，保留阻挡。 |
| Taipa | 3,276 | 使用同字节的历史文件名副本。 |
| Tinggede | 2,601 | 使用同字节的历史文件名副本。 |
| Darat Sekip SF | 4,701 | 使用同字节的历史文件名副本。 |

三个 named paper 问题已追溯到 `e682de2` 的角色分类修复与未同步迁移的旧配置。
所有 paper 实体仍在证据中；需要明确是否属于工程范围，才能生成新的 reviewed pack。
随附 [迁移提案](upgrade-baseline-migration-proposals.json) 不是已批准的生产配置。

各图最终转换、源图逆算审计、未解决项和 `CONDITIONAL` 状态，见交付中的逐图报告。
OSM 相对配准没有独立测量参考；0.01 m 按钮表示操作步长，不是厘米级绝对精度承诺。

## 复用交付的源快照

完整证据包保留了 DWG 副本、原配置、原始 records、原始空间层和绑定清单。
解压到新位置后，先在源码环境中重定位 manifest：

```bash
python tools/relocate_source_snapshot.py /absolute/path/source-snapshots/kletek/source
cad2gis query-source /absolute/path/source-snapshots/kletek/source --limit 20 --json
```

重定位器先检查每个 artifact 的内容哈希，保留原 manifest，只更新位置元数据；
不会改变 reader authority、source facts 或 snapshot identity。损坏的数据会拒绝重定位。
旧 reader 生成的快照如果 reader identity 不一致，仍需要重新导出。
