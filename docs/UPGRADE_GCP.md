# GCP 精细校正与持久化

本轮升级接入现有 CAD/map 配对、review SQLite/PostGIS 和 OSM rematch 工作流。
`gcp_workflow.edit_web_control` 管理 target 编辑，`review_server` 在数据库事务内执行校验和写入。
源 DWG、source/evidence/delivery GeoPackage 不被编辑。

## 地表微调

地图中的 `0.01 / 0.1 / 1 m` 按钮表示 WGS84 ellipsoid 上的地表距离。`east / north /
west / south` 分别使用方位角 `90 / 0 / 270 / 180°`，由 `pyproj.Geod.fwd` 求出新 longitude/
latitude，再由现有 `Transformer(always_xy=True)` 转入交付 CRS。

不能直接把 EPSG:3857 的 X/Y 加上这些数值：Web Mercator 的 grid metre 与地表 metre
不同。球面近似下，局部 ground distance 约为 grid distance 乘 `cos(latitude)`；正式微调
使用 ellipsoidal geodesic，不使用这个近似。越过 antimeridian 时 longitude 保持合法范围；
正负 90° 的极点不支持有歧义的 cardinal direction 微调。

小步长用于精细操作，并不证明数据有厘米级绝对精度。屏幕像素、选点误差、OSM reference
质量和测量来源仍决定能验证到的精度。

## API

```http
POST /api/review/features/gcp:one/adjust
Content-Type: application/json

{"operation":"nudge","direction":"east","step_m":0.01,"expected_revision":3}
```

`operation` 也可以是 `undo` 或 `reset`，此时不需要 direction/step。响应沿用现有 upsert
event：`event_id / feature_id / operation / revision / payload / created_at`。WebSocket 同步
广播相同 event，前端重新获取 controls 并使旧 preview 失效。

| 行为 | 契约 |
| --- | --- |
| 新 GCP | 显式 `expected_revision: 0` |
| 编辑、删除、微调 | 必须携带当前非负 integer revision |
| 陈旧 revision | HTTP 409，无 feature/event 写入 |
| 非法坐标、role、step、history 或 JSON | HTTP 400，无写入 |
| 已删除或不存在的微调对象 | HTTP 404，需要刷新控制点 |
| CAD source | `cad_x/cad_y` 与已有 source provenance 不可改；重新选源点需要新配对 |
| 初始 target | 由第一次保存生成，普通客户端不能覆盖 |
| Undo | 持久化最多 128 次 target 编辑；reset 本身也能撤销 |

editor state 位于 `properties._gcp_edit`，包含 `schema_version: 1`、`initial_target`、
`undo_targets`、`last_action`、`distance_method` 和可选的 `last_nudge`。
SQLite 重启保持记录，PostGIS 使用相同状态变更函数。PostGIS 采用 transaction advisory
lock，覆盖尚不存在的记录，避免两个客户端同时创建 revision 1。坐标序列化保留 15 位小数。

OSM match profile 将 `editor_state` 纳入 controls 和 match SHA。单独打开新结果时，内部
importer 从已绑定的 record 恢复初选和历史；公开 upsert API 不能使用这个 importer 入口。
历史超过 128 次时只裁剪 undo stack，initial target 不变。完整 event audit 仍在 review store。

## 残差和验证边界

Training controls 用于拟合 transform；held-out checks 不参与当前拟合。对一组点，

\[
\mathrm{RMSE}=\sqrt{\frac{1}{n}\sum_{i=1}^{n} d_i^2}.
\]

界面分别显示 training 和 check 残差。OSM matching 返回的 `error_m` 使用 WGS84
ellipsoidal geodesic；legacy canonical GCP fit 使用 target projected grid metres，响应
通过 `residual_distance_method` 明确区分。没有 check 时指标为 `null`，不能用 training
低残差替代独立验证。这里的 held-out 指“不参与当前拟合”，不是对所有历史操作和采样方法
统计独立性的证明。

`GET /api/registration` 返回 `precision_contract`，明确
`nudge_step_is_accuracy_claim: false`、`source_cad_pick_mutable: false` 和 absolute accuracy
未验证。OSM reference 的默认 `accuracy_m: 8` 是为了兼容 canonical fitter 的 uncertainty
prior，不是测量得到的 OSM 精度或输出精度；`accuracy_basis` 标记来源，operator 可以显式
提供自己的 uncertainty estimate。

legacy profile activation 的 preflight 要求至少 4 个不同 training source/target 和 3 个
不同 check source/target，且两组 source 点具有非退化二维分布。斜向共线点不再因 X/Y 均有
span 而通过。与 training 复用 source 或 target 坐标的 checks 被标记，拒绝作为 held-out
fit 输入；disabled controls 不参与计数和拟合。OSM 的 translation preview 仍支持其原有
两个不同 training points 的最小输入，不宣称通过工程验收。

Export 保留已绑定 source profile 的全部 spatial coverage policy，不自动放松阈值；最终
conversion 继续执行原有 coverage 和 check gates。提供 `expected_controls_sha256` 的 export
会拒绝旧 preview。输出仍标记 `RELATIVE_OSM_REFERENCE_ONLY`，不会变为 surveyed accuracy。

## 可核查的中间层

`GET /api/matching/evidence` 返回当前 match SHA/revision 及 `collections`：
`cad_routes / osm_roads / cad_nodes / osm_nodes / residuals`，均为 EPSG:4326 GeoJSON。
可传 `expected_match_sha256`，版本改变时返回 409。无 evidence 的旧 record 返回空图层。
manual residuals 保留 point ID 与 train/check role；OSM 拓扑解释见 `UPGRADE_OSM.md`。

Review 的 CAD pane 继续读取已持久化的 feature/segment curve materialization，保留拆分后
feature 身份。缺少完整 curve materialization 的 legacy 曲线不能静默显示成直线弦。
读取主 run 和 evidence 时统一校验 SHA-256；复制的 run 优先使用当前目录内已绑定的文件，
不因旧绝对路径仍存在而读取另一份图纸。

## 验证

```bash
python -m pytest tests/test_gcp_editing.py tests/test_gcp_workflow.py \
  tests/test_review_server.py tests/test_review_native_geometry.py -q
```

覆盖 0/60/80° latitude 下四方向和三档步长、antimeridian、持久化 undo/reset、陈旧 revision、
SQLite 两个独立 store 实例的并发冲突、非法输入、CAD provenance 不变、共线/重复控制点、
新 match workspace 历史恢复、原 coverage policy 不变和 artifact tamper。
PostGIS 实现共享相同编辑契约，但此运行环境未连接真实 PostGIS 服务；真实数据库并发和部署
仍需要配置服务后的 integration test。UI/browser 和九份 DWG 的验收由整合流程报告。

## 参考文档

- [pyproj Geod](https://pyproj4.github.io/pyproj/stable/api/geod.html)
- [QGIS Georeferencer](https://docs.qgis.org/latest/en/docs/user_manual/managing_data_source/georeferencer.html)
- [QGIS control point placement](https://docs.qgis.org/latest/en/docs/training_manual/forestry/map_georeferencing.html)

文档核对日期：2026-09-09。
