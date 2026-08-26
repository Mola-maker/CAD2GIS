# HAOQI 参考站拆解与 CAD2GIS 替换方案

> 参考源：<https://haoqi.design/>（本地检查于 2026-08-26）
> 本文只记录结构、节奏、动效机制和可替换的设计约束，不复制原站品牌、文案、模型或图片资产。

## 1. 参考站的可迁移结构

| 参考站段落 | 视觉结构 | CAD2GIS 替换 |
| --- | --- | --- |
| Hero | 固定网格、软蓝背景、中央 3D 主体、巨型黑色标题、单色 CTA | 固定 CAD 网格、证据图 SVG、`1,150 FEATURES` 与 `CONDITIONAL` 状态、打开真实演示 |
| Intro | 人像/物件与大段叙事错位排版 | Hutabohu 真实运行摘要：9,896 源实体、8 个交付图层、14,702 证据节点 |
| Project grid | 不对称卡片网格，项目名/角色/状态 | Source Facts / Semantic Mapping / Registration / Validation / Delivery 五个阶段卡片 |
| Tunnel | 黑底径向射线，巨大中心文本，滚动时视觉切换 | “几何正确，不等于坐标准确”黑色验证隧道；四条独立验证轨道 |
| Infinite field | 中央原则文案，周围星线/环形元素和漂浮标签 | Evidence Graph 无限场：源事实、不可变几何、独立测量、可回放交付 |
| Final CTA | 蓝色 3D 物体、贴纸式辅助元素、短 CTA | 派生演示、证据链、GitHub 三个入口；明确“公开页不含 DWG/GPKG 原始文件” |

## 2. CAD2GIS 产品叙事

核心承诺：**把不可信的 DWG，变成可验证的 GIS 交付。**

副标题：CAD2GIS 从不可变源事实出发，分别验证几何、拓扑、长度和坐标精度，再生成可追溯、可重放的 GeoPackage 交付。AI 负责理解与提案，不改写坐标。

真实运行标签：`HUTABOHU REAL DERIVED RUN · CONDITIONAL · 1,150 FEATURES`

五个阶段：

1. Source facts：SHA256、实体清单、样式、标签和原生曲线事实。
2. Semantic mapping：来源绑定的 profile/registry，未解析实体不被伪装成已分类。
3. Registration：训练点、独立检查点、相似变换、残差和覆盖度。
4. Independent validation：源几何、拓扑、长度、坐标精度四个互相独立的检查轨道。
5. Delivery：GeoPackage、QML、manifest 和证据图，所有动作可回放。

## 3. 动效状态机

| 状态 | 触发 | 动效 | 数据含义 |
| --- | --- | --- | --- |
| `hero-intro` | 页面进入 | 节点由中心向外绘制，路径 dash offset 归零，标题逐行上浮 | 从 DWG 源事实开始 |
| `evidence-hover` | 鼠标/触控移动 | 证据节点轻微视差，连接线亮度跟随最近节点 | 不改变几何，只强化可读性 |
| `stage-reveal` | 阶段卡片进入视口 | 当前阶段变亮，连接线从虚线转实线，序号环扩散一次 | 说明流水线的单向证据积累 |
| `metric-count` | 指标进入视口 | 只播放一次的数字计数 | 数字来自真实派生 fixture，不模拟实时计算 |
| `validation-tunnel` | 隧道进入视口 | 径向线缓慢旋转，四条验证轨道依次点亮 | 几何/拓扑/长度/坐标精度独立，不合并为单一分数 |
| `delivery-cta` | 最终区进入视口 | 蓝色光晕呼吸，入口卡片轻微浮动 | 指向真实 demo、证据说明和仓库 |

所有状态都必须受 `prefers-reduced-motion: reduce` 控制：禁用视差、旋转、计数和路径延迟，但保留最终可读状态。

## 4. SVG 设计清单

- `hero-evidence-graph.svg`：源事实到交付 manifest 的可编辑路径、节点、箭头和状态环。
- `hero-grid.svg`：低对比度 CAD 网格和十字准星，作为固定背景图层。
- `hero-tunnel.svg`：四条验证轨道和径向射线，黑底区专用。
- `hero-stickers.svg`：`SHA256`、`GCP`、`DIMENSION`、`GPKG` 四个小型标签，不使用参考站原始贴纸。

SVG 由本项目脚本生成或手写为 repo-native vector；不下载/嵌入参考站的私有模型、字体、图片或品牌资产。

## 5. 真实数据边界

- 源名称：`APD - DUSUN MENARA DAN PUSAT HUTABOHU GORONTALO.dwg`。
- 源实体：9,896；交付图层：8；交付要素：1,150。
- 证据图：14,702 节点、20,074 条关系。
- 31 条源 cable curves；179 条交付段，其中 170 条有独立 DWG DIMENSION，9 条只有 CAD geometry length。
- 运行状态：`CONDITIONAL`；281 个实体未解析。
- EPSG:3857 只是名义坐标域；没有测量 GCP，所以绝对坐标精度未独立验证。
- 公开页面只使用筛选派生证据，不上传 DWG、GPKG 或本机路径。

## 6. 与代码的映射

- CSS tokens → Figma Foundations：`color/*`、`space/*`、`type/*`、`motion/*`。
- Hero 页面 → Figma `Hero / Product Infinite / Validation Tunnel / CTA` 四个 section frame。
- `EvidenceNode` → Figma component set：`Kind=source|semantic|registration|validation|delivery`，`State=idle|active|verified|conditional`。
- `StageCard` → `Stage=01..05`，文案和指标绑定为文本属性。
- `StatusPill` → `Tone=teal|amber|violet|ink`，`State=default|active`。
- 代码里的 CSS motion 是最终运行时真相；Figma motion 记录用于审查和 handoff，不把 Figma 原型当作浏览器运行时。
