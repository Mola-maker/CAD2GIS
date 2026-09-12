# Lamteh / Tinggede 偏移调查与正式重建

2026-09-12，工作区基于 `ed93cd2`，调查对象为外部
`E:/github-CAD2GIS/baselines/{lamteh_main,lamteh_sf,tinggede}`。

## 结论与生成来源

偏移的直接原因是自动匹配未通过验收后保留原始坐标，复核校正又没有进入批量重建。
发布流程当时仍允许这些结果生成 OSM review QGZ，且固定的 nominal 标题不能准确
说明正式转换使用过何种匹配。不能单凭标题判定算法是否执行。

三个外部原始备份 QGZ 与本仓库修复前的受版本控制 QGZ 完全同哈希：

| 项目 | 原始 QGZ SHA-256 |
|---|---|
| lamteh_main | `cee7c2ab4d038bec97ca6d20a3eff6ac1ccc93b4d2130ea788d1e771d4a340e5` |
| lamteh_sf | `19bcd493198b4e62c62a266d541c404644576dda763bcc3feadc738e0c4b111b` |
| tinggede | `62d8234517221bf0020de9f2c7ec93b80325dabb141e0178b64034be10cf8cc8` |

这组受版本控制的交付来自提交 `1d40862`；提交说明明确记录了“缺部分项目 GCP 微调”。
[前次重建记录](RESOURCE_USAGE_BASELINE_RERUN_2026-09-11.md) 描述的生成链为：
冻结 `baselines/*/config` → `tools/verify_upgrade_corpus.py` → 正式转换 →
`publish-match` → 独立 SVG QGZ。历史提交 `0df9e7a` 已保存 Tinggede 人工匹配文件，
但使用说明依赖显式 `--match-profile`，上述批量脚本没有传入该参数，项目解析层也没有
自动读取此选择。因此配置文件虽然存在，重建仍未使用。

历史记录中的 Linux `/home/cat/...` 运行树不在本机；本机 WSL 也没有该目录。
本调查依据 Git 历史、原始文件哈希和本次重跑定位来源，不声称已读取已删除的远端日志。

## 原始 DWG 与离线复现

| 项目 | 原始 DWG SHA-256 |
|---|---|
| lamteh_main | `d71ad87e6c0c81811725cf6238dd883621f0d09c03815c81eebd067eec1f8e78` |
| lamteh_sf | `5b0872935a070cac9b3969811799d229bb48e534085254930263d526db9c5735` |
| tinggede | `f71526ec36d503d07f883d22fdadaa9c3ccdc30be804402df4c533309f664a5b` |

Tinggede 原图在本机下载目录找到，并可从历史 Git 对象恢复相同字节。三图均从原始 DWG
完成实际转换，没有用旧 QGZ 的显示偏移替代转换。

自动诊断复用各项目 `config/.osm-cache` 中经哈希验证的道路。诊断适配器把冻结的
EPSG:3857 道路直接交给道路获取接口，并记录请求边界；算法及阈值未修改。
该缓存是 `{bbox, roads, sha256}` 格式，不能当作接受 `{elements}` 的 `--osm-data`
Overpass 文件。初次 SF 诊断误用此输入得到 unavailable，已排除，以下使用格式正确的重跑。

| 项目 | 自动候选平移，EPSG:3857 米 | 未采用原因 |
|---|---|---|
| lamteh_main | (+31.88918, +45.62469) | 有效路线支持比例 39/53 = 0.73585，低于 0.75 |
| lamteh_sf | (+29.79780, +47.22677) | 单条路线方向信息不足，最小方向特征值 0.03880，低于当时源码的 0.04 |
| tinggede | (-5.83182, -16.44178) | 两个候选分差 0.05354，低于 0.06 |

三者状态均为 `abstained`，实际自动平移为 (0,0)。候选接近合理位置说明自动搜索具有
定位能力，但不等于它已满足采用条件。此修复没有降低验收阈值。

## 已应用修复

1. 项目解析支持 `config/cad2gis-project.json` 的 `config.match_profile`，或通用文件名
   `match_profile.json`。显式参数优先；源图和原始变换绑定仍由已有加载器验证。
2. 三个项目分别固定已复核的匹配文件。Lamteh 两份新增独立封存记录；Tinggede
   复用原有 `matching-manual-gcp.json`。批量重建记录并核验所选文件摘要。
3. `publish-match` 拒绝非空范围内未匹配成功的结果；SVG 和 QGIS 发布阶段均跳过空范围。
4. QGZ 和旁边的 review 收据保存正式匹配记录、源图/运行清单/数据库摘要。标题准确显示
   OSM 状态和版本；独立重开验证器检验这些绑定。
5. 实测修复了 OSGeo4W 启动器清空 PYTHONPATH 导致源码模块无法加载，以及 SQLite
   只读连接未关闭导致 Windows 临时打包文件被占用的问题。

Lamteh 的依据来自 SF 源图中两个有明确设备身份的坐标表，详见各项目
`config/matching-source-coordinates.evidence.json`：

- P001：身份属性 `1CC2AC`，坐标属性 `1CC2AE`，共同所属块 `1CC2AA`；目标
  (95.368299, 5.468081)，实体 `1AC878`。
- P137：身份属性 `1CC30A`，坐标属性 `1CC30C`，共同所属块 `1CC308`；目标
  (95.361535, 5.468867)，实体 `1B051C`。

两张 Lamteh 图的共同实体原生坐标与 GEODATA 变换一致，因而采用相同平移，但匹配
记录各自绑定不同的 DWG 摘要。另一处重复坐标表未被当作独立控制点。

## 正式重建及验证

实际调用只提供源图、项目、新运行目录与 `matching='osm'`，没有显式传入匹配文件。
运行清单确认三个项目默认加载了固定配置，输出匹配记录与配置逐项相同。

| 项目 | 最终平移，EPSG:3857 米 | 要素数 | 顶点数 | 训练 RMSE，地面米 | 独立检查 RMSE，地面米 |
|---|---|---:|---:|---:|---:|
| lamteh_main | (+35.25554, +48.88529) | 1000 | 2318 | 4.81735，2 点 | 无 |
| lamteh_sf | (+35.25554, +48.88529) | 56 | 106 | 4.81735，2 点 | 无 |
| tinggede | (-4.66929, -16.72220) | 515 | 1390 | 4.36490，5 点 | 7.48115，3 点 |

三份正式匹配均为 `matched` 第 2 版。所有 3814 个顶点相对自动未校正结果的位移符合
配置，最大数值偏差小于 0.00001 投影米。要素身份、业务长度及其来源、原图尺寸值
保持一致；坐标、派生几何长度、方向样式和变换来源记录由正式转换重新计算。

QGIS 4.2.2 独立进程打开与离线渲染全部通过，SVG 绑定数量为 248 / 28 / 74。
正式 GPKG 与打包内嵌 GPKG 哈希一致。参考 OSM 道路复用原 QGZ 已有几何、ID 和名称，
没有重新下载；重建的类别标签只用于绘图，不参与匹配。

回归测试：171 passed，5 skipped。跳过原因为 Windows 不允许创建测试符号链接（1项）
和缺少可选 Hutabohu 基线证据（4项）。变更文件 Ruff 检查通过，代码复核发现的问题均已处理。

**验收边界：** 三份运行总状态仍为既有 `CONDITIONAL`，本次解决路网偏移，不代表全部
语义/交付门禁均通过。`absolute_accuracy_verified` 仍为 false；Lamteh 两点仅约束平移，
没有独立检查点。Tinggede 检查点用于相对定位检验，也不是测量级绝对精度证明。

## 本机目录与再次运行

- 原始未校正备份：外部各项目 `delivery/view-with-source-SVG.before-alignment-20260912.qgz`。
- 上一轮临时交付修补备份：外部各项目 `delivery/before-canonical-rebuild-20260912/`。
- 正式交付：外部各项目 `delivery/view-with-source-SVG.qgz`；同目录还有可编辑的
  `delivery.qgz`、`delivery.gpkg`、`osm-basemap.gpkg` 和两个 review 收据。
- 精简证据归档：外部各项目 `review/alignment-20260912/`，包含自动/最终匹配记录、运行清单、
  几何验证及渲染收据，不附带完整源图证据库。
- 完整本次运行：工作区 `tmp/generation-investigation/<项目>/automatic-run/`（SF 为
  `automatic-cache-run/`）和 `pinned-run/`；原生提取及 SVG 候选仍在这些运行目录。

使用本次修复后的 CAD2GIS，在新的运行目录重建即可复用校正：

```shell
python -m cad2gis convert "<SOURCE.dwg>" --project "E:/github-CAD2GIS/baselines/<项目>" --matching osm --run-dir "<NEW_RUN>"
python -m cad2gis publish-match "<NEW_RUN>" --delivery-root "<NEW_DELIVERY_ROOT>" --osm-data "<项目>/review/alignment-20260912/review-osm-snapshot.json" --qgis-python "E:/bin/python-qgis.bat"
```

旧版已安装包不会自动获得工作区代码修复。已归档到 `tmp/repair_baseline_alignment.py` 的工具是此前
对独立交付包的应急修补工具；这里交付的新版由正式原图转换及发布流程产生。

## 后续：按要求降低自动阈值并推送

用户随后明确要求降低阈值。本次将有效路线支持比例从 0.75 调整到 0.70，候选分差
从 0.06 调整到 0.05，最小方向特征值从 0.04 调整到 0.035。上文描述此前诊断与交付
重建时的行为；此前口头说明将方向阈值写成 0.05 不准确，已按源码更正。

覆盖率 0.75、方向一致性 0.70、25 米道路走廊、收敛检查、搜索预算检查和重复网络
歧义检查保持原有规则。数值常量同时用于验收和匹配记录中的阈值，避免记录与执行不一致。
真实源图路线与原始冻结路网的回放作为 `tests/test_baseline_osm_thresholds.py` 回归用例，
无需 DWG 或联网即可执行。该测试还核验项目选择的封存配置、坐标表的身份/坐标配对
及真实坐标变换，防止源图依据再次只存在于说明文件而未被配置加载。

降低阈值影响没有固定校正时的自动匹配。三份交付仍优先使用已核验的第 2 版配置，
其中 Lamteh 使用源图坐标依据，Tinggede 使用已有人工校正。不会把自动候选覆盖到
已保存的源图控制结果上。

下调后的三份真实路网回放均通过，自动平移分别为 (+31.88918, +45.62469)、
(+29.79780, +47.22677)、(-5.83182, -16.44178) 投影米。配置固定的第 2 版平移
仍按上表执行。此次相关两组测试合计 294 passed、1 skipped（Windows 测试符号链接），
包括重复路网、平行几何、候选歧义、真实数据回放、配置绑定、转换及交付回归；全项目
Ruff 检查通过，独立代码复核没有剩余问题。

推送前发现远端新增 `c9626bb` 刷新了十组基线。已比较其三份目标 QGZ：虽然压缩包
摘要改变，内嵌 CAD GPKG 摘要与本次调查的原始未校正版本完全相同。本次整合保留
其他项目刷新，并让三个目标项目使用本次已验证的校正交付。
