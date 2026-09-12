# 补丁读取器后的源清单复核

本次使用已安装的最终 `0.7.0` wheel 和已完成五轮真实原文验收的 Windows 补丁读取器，对 Darat、Manado 新建源快照。使用 Git 原始配置的副本和原绑定文件名，不复用旧读取记录、不改 baseline。两个原件及 64 个 Git baseline 文件哈希均未变化。

## 结果

| 工程 | 新源实体数 | Model 实体 / INSERT / DIMENSION | 完整清单绑定 |
| --- | ---: | --- | --- |
| Darat Sekip SF | 4,701 | 2,395 / 35 / 12，与原批准计数一致 | 仍不匹配 |
| Manado–Tomohon uplink | 5,057 | 2,788 / 125 / 43，与原批准计数一致 | 仍不匹配 |

两次源导出成功，随后均在 `validate_project` 因 profile 和 registry 的旧清单绑定被阻断，没有新转换产物。原生文本稳定性修复与“新事实可继承旧语义批准”是两项不同验证；计数相同不足以证明完整清单相同。

| 工程 | Git 批准 inventory SHA256 | 补丁读取的新 inventory SHA256 |
| --- | --- | --- |
| Darat | `488e5126ea596513dedcd0c65f472fc589fb8ca0eb96664c6205616d72a8dd04` | `9a14bb25e7291af8845792a94e764cf403565b9b4cd09800f2e55a4d485c71a8` |
| Manado | `c19e4259ed8c6348c0ba4755814248d307332a5cd8c4573a2b69f12b1bc74630` | `9923360a0789ca3754e806cbfa2b4e7c7b138038fb49da7ae3cbc1ccefc922ea` |

之前 Darat 的 28 要素成功记录使用精确绑定的封存事实重放，仍然有效，但不代表补丁后重新读取可以直接沿用旧批准。Manado 也没有获得新的语义批准。本次保留这些阻断，供后续以新快照逐项审阅后产生新的项目修订；不得仅替换 hash 把旧批准套到新事实。

## 与跨平台原生验收的关系

Windows/Linux 各五轮原生复测的 JSON 对象及 MTEXT 一致。平台补充对照发现原始 DXF 的 420/421 颜色整数符号写法在平台间不同，但相应整数位值相同；其余标签一致。原始输出保留，未为了得到相同哈希篡改源记录。详见平台的 `tmp/native-patched-cross-platform-explained.json`。

## 证据

目录：`E:/branch_CAD2GIS/validation/native-corpus-final-20260912/`。

- `reference-before.json`：实际运行时、二进制哈希、原生验收和 baseline 初始哈希。
- `binding-recheck.json`：实际/预期清单、计数和原件/baseline 保留核验。
- `runs/summary.json`、各工程 `report.json`：执行阶段和原始阻断。
- 各工程 `source/` 与 `project/review/source_inventory.json`：新快照与完整清单，仅保留在本地验证目录。

本复核不改变 [九图回归](CORPUS_UPGRADE_2026-09-11.md) 的历史运行口径，也不把安装环境就绪写成九张图纸全部通过。
