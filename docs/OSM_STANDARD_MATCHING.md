# OSM 标准匹配与人工二次校正

CAD2GIS 的标准流程是 **OSM 自动匹配 → SVG 图例候选提取 → 查看 GIS 与候选 → 人工控制点预览 → 确认后二次匹配 → 自动加载新结果**。
最终交付使用所选匹配坐标，并记录 `authority: relative_osm` 和
`absolute_accuracy_verified: false`。地图重合、较小的拟合残差或较高的自动匹配分数，
均不代表已证明测量级绝对精度。

本页的命令是使用说明，尖括号内容需要替换为本地文件或目录。示例不表示已经转换了
某张用户 DWG。运行环境安装见 [README](../README.md#快速开始)；每个 DWG 都需要
自己的源配置和语义映射，OSM 定位不替代这些前置检查。

## 1. 生成第一轮结果

已有验证通过的项目配置时：

```shell
cad2gis validate --project "<PROJECT_DIR>" --json
cad2gis convert "<SOURCE.dwg>" --project "<PROJECT_DIR>" --run-dir "<FIRST_RUN_DIR>" --json
```

`convert` 与 `auto-convert` 的默认匹配模式都是 `osm`。有效的原始坐标域可用于确定
OSM 道路搜索范围；只有本地坐标的图纸，需要显式地点、已有项目地点提示或具有地理
坐标的 OSM 道路快照。系统不会从文件名隐式猜测城市。

SVG 提取同样默认开启，使用 `candidate` 模式：原图符号、图例和交付点的对应候选保存在
`<FIRST_RUN_DIR>/svg-candidates/`，包含候选数据库、JSON 报告和 `correspondence.html`
并排复核页。该步骤不自动应用符号样式。需要原图字体时，可重复添加
`--svg-font-dir "<FONT_DIR>"`；显式 `--svg-mode off` 关闭提取。
GIS 与 SVG 候选完整生成后一起发布；提取失败不会留下半成品新 run。
对应状态、来源和文件摘要记入运行清单，细节见 [SVG 提取与符号绑定](OPTIONAL_SYMBOL_ASSETS.md)。

### 使用离线道路快照

```shell
cad2gis convert "<SOURCE.dwg>" --project "<PROJECT_DIR>" --run-dir "<OFFLINE_RUN_DIR>" --osm-data "<OVERPASS.json>" --json
```

`--osm-data` 接收已保存的 Overpass JSON 响应：带 `highway` 标签的 `way`，既可以
直接包含经纬度 `geometry`，也可以通过 `nodes` 引用响应中的节点。匹配读取道路形状，
并在匹配记录中保存采用的道路快照和摘要。该参数让道路匹配无需访问在线 OSM 服务；
Web 工作台的 OSM 栅格底图仍需要浏览器连接底图服务。

### 使用显式地点提示

```shell
cad2gis convert "<SOURCE.dwg>" --project "<PROJECT_DIR>" --run-dir "<PLACE_RUN_DIR>" --osm-place "<PLACE_NAME>" --json
```

该参数显式指定搜索地域，也可用于纠正现有标称坐标对应的搜索位置。地点应足够具体；地点中心
本身只提供粗定位。在线服务配置及公共服务的使用要求见本页后文。

## 2. 查看结果并预览人工校正

```shell
cad2gis review "<FIRST_RUN_DIR>" --workspace "<REVIEW_DIR>" --port 8765
```

打开 [本地工作台](http://127.0.0.1:8765/)。左图保留 CAD 原生坐标，右图首先显示当前
GIS 结果。核对设施、道路拐点、缆线走向与地图位置后，按需添加人工控制点：

1. 在左图选择真实 CAD 实体上的对应点；点击会吸附到实际几何。
2. 在右图选择同一位置，或输入 EPSG:4326 经度和纬度。
3. 将用于计算平移的点设为训练点，将独立验证用的点设为检查点。
4. 保存至少两个位置不同的训练点后查看校正预览和残差。

保存控制点只更新审查工作区与预览。右图的人工校正预览由服务端使用正式匹配变换
计算，检查点不参与拟合。新增或删除点会重新计算预览；点不足或输入失效时，界面会
显示原因并保留上次可用视图。预览残差很小时，也仍应检查控制点是否对应了同一真实位置。

## 3. 确认并生成新的 GIS

确认预览后，点击 **“二次匹配并生成新结果”**。服务会冻结本轮人工点与匹配记录，
检查源文件、原始配置和当前匹配版本，再运行正式转换。

- 成功后，工作台自动加载新生成的 GIS，显示新的匹配版本，并保留人工点供下一轮修改。
- 转换期间控制点编辑被锁定；另一页面使用过期匹配版本或控制点摘要提交时，会要求刷新。
- 转换失败时，当前结果和控制点保留；修正问题后可重试。
- 已发布 run 不可覆盖。每次转换使用新目录，并保留前一版结果与来源记录。

二次匹配沿用本轮 SVG 模式和字体目录，为新 GIS 重新生成候选；候选与新数据库的哈希
绑定一起更新。新 run 的 SVG 提取失败时，当前 GIS 和旧候选保留供继续复核。

控制台显示本次匹配配置路径、结果位置和可复制的转换命令。重新打开同一个审查工作区
可以恢复当前匹配结果；也可单独打开生成的 run，其保存的人工点会用于初始化新的审查工作区。
如需证明测量级精度，应另用测量或权威控制点及独立检核流程。

## 4. 匹配模型和独立图框

当前 OSM 模型包含固定的基础坐标操作与一个总平移。基础操作负责 CAD 单位和必要的
投影转换；本地 CAD 定位同时处理参考纬度对应的 Web Mercator 比例。自动匹配对多个
道路或缆线路段共同评分，人工校正使用训练点重新求总平移。

每次迭代均从原始 CAD 坐标和同一个基础操作计算位置，不把上一轮输出当作新的 CAD
坐标。当前模型不自动求旋转、不做拉伸、仿射或橡皮片变形，也不沿 OSM 道路逐点吸附
缆线。CAD 源几何及源长度事实保留，GIS 的坐标与派生网格、椭球长度按所用变换计算。

同一张 DWG 中配置的独立图框分别匹配，不将它们合为一张道路网络拟合。可以用
`cad2gis review "<PARTITION_RUN_DIR>"` 打开某个已有分区。该分区生成的匹配配置会绑定
区域身份，二次匹配只发布该区域的新交付，不把该区的控制点平移应用到其他独立图框。

自动匹配状态需要结合地图检查：

| 状态 | 含义与下一步 |
| --- | --- |
| `matched` | 自动网络匹配通过相对匹配门槛，或已计算人工平移；仍需核对地理对应关系。 |
| `coarse` | 地域可用，但道路不足、网络拟合较弱或存在多解；保留粗定位以供人工校正。 |
| `unavailable` | 缺少可用匹配输入或可匹配路线；有可信标称坐标时可查看其结果，本地 CAD 需补充定位输入。 |
| `abstained` | 自动候选未获接受；查看诊断并补充参考，不应把候选解释为已确认匹配。 |

人工匹配记录中的 `needs_review` 和训练、检查残差用于提示进一步复核；它们不提供
测量绝对精度认证。

## 5. 参数与兼容入口

| 参数 | 用途 |
| --- | --- |
| `--matching osm` | 默认标准模式：尝试 OSM 相对匹配。 |
| `--matching nominal` | 使用原标称坐标兼容流程，保留其现有验证要求。 |
| `--osm-data "<OVERPASS.json>"` | 使用离线 Overpass 道路快照。 |
| `--osm-place "<PLACE_NAME>"` | 显式指定地点搜索地域，优先于标称坐标推定的在线范围。 |
| `--match-profile "<SAVED_MATCH_PROFILE.json>"` | 使用完整、原样保存的匹配记录，重放同一基础坐标操作和匹配平移。 |
| `--gcp-profile "<GCP_PROFILE.json>"` | 显式使用已有 GCP 校准流程，仍须满足其训练、检查与精度验证要求。 |
| `--svg-mode candidate` | 默认提取原图 SVG 与图例对应候选；不自动应用到交付样式。 |
| `--svg-mode off` | 显式关闭 SVG 提取；不传字体目录。 |
| `--svg-font-dir "<FONT_DIR>"` | SVG 提取所用的原图字体目录，可以重复指定。 |

标准 OSM 模式不会自动启用项目目录中发现的 `gcp_profile.json`。使用 `--gcp-profile`
是显式选择 GCP 流程；`--matching nominal` 保留原有项目 GCP 发现行为。
`--matching nominal` 不能与 OSM 输入混用。`--match-profile` 不能同时传入新
`--osm-data`、`--osm-place` 或 `--gcp-profile`。

例如，选择标称坐标兼容流程：

```shell
cad2gis convert "<SOURCE.dwg>" --project "<PROJECT_DIR>" --run-dir "<NOMINAL_RUN_DIR>" --matching nominal --json
```

对于只有已验证源配置与映射的常规项目，保存的匹配记录可这样重放：

```shell
cad2gis convert "<SOURCE.dwg>" --project "<PROJECT_DIR>" --run-dir "<REPLAY_RUN_DIR>" --match-profile "<SAVED_MATCH_PROFILE.json>" --json
```

如果原运行还使用了 Decision Pack、原生源快照或已发布语义版本，应使用工作台返回的
完整命令，保留这些输入和模式。`--match-profile` 绑定的是匹配操作，不代替整个项目的
转换输入。不要手动编辑匹配记录：其摘要绑定源文件、源和目标 CRS、单位、基础操作、
版本与道路快照；记录或绑定输入变化会导致校验失败。

Python 的 `convert_project` 和 MCP 的 `run_conversion` 同样提供 `matching`、
`osm_data`、`osm_place`、`match_profile` 参数，调用同一条生产转换管线。
这两个入口以及 MCP `auto_onboard_and_convert` 也共享 `svg_mode` 和 `svg_font_dirs`；
两个 MCP 转换工具在返回结果中提供 `svg_candidates`，可直接打开对应复核报告。

## 6. 来源记录与服务配置

匹配结果记录在 run 的 `osm_matching.json`、运行清单及相关坐标来源中。人工迭代还
记录父匹配摘要、父运行摘要、训练点与检查点、控制点摘要及残差。检查时应同时看
匹配状态、坐标来源和独立检核，不能把 `matched` 读成测量验收通过。

在线地点检索使用 Nominatim，道路查询使用 Overpass。可通过以下环境变量配置部署：

| 环境变量 | 含义 |
| --- | --- |
| `CAD2GIS_NOMINATIM_URL` | Nominatim 兼容的地点查询端点。 |
| `CAD2GIS_OVERPASS_URL` | Overpass 查询端点。 |
| `CAD2GIS_OSM_USER_AGENT` | 标识应用及运营方的 HTTP User-Agent。 |

使用公共 Nominatim 服务前，应阅读其
[官方使用政策](https://operations.osmfoundation.org/policies/nominatim/)。公共服务要求
应用总体不超过每秒一次请求、使用可识别应用的 User-Agent、缓存重复查询，并允许切换
服务端点。CAD2GIS 标准匹配提供地点缓存及同一进程内的公共服务节流；多进程或多机部署
仍需在应用层统一控制总量。较大规模的持续查询应使用自建或适合该用途的服务。

OSM 数据和输出中涉及的 OSM 参考应保留 **© OpenStreetMap contributors** 署名及
ODbL 信息，具体要求见 [OpenStreetMap 版权与许可](https://www.openstreetmap.org/copyright)。
匹配记录保存对应署名；人工校正始终标为 OSM 相对参考。

## 与可选 QGIS 复核工具的关系

本流程通过正式转换生成使用匹配坐标的新 `delivery.gpkg`。已有的
[QGIS / OSM 复核工具](qgis-osm-review.md) 则可给 QGZ 加入离线底图，并在复核工程内
应用显示校正。选择入口时，应根据需要生成新的 GIS 坐标，还是只复核已有工程显示来决定。
