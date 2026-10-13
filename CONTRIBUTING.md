# 参与 CAD2GIS

欢迎提交 CAD/GIS 复现、兼容性修复、文档与 Agent 工作流。提交贡献即表示你有权以项目的 Apache-2.0 许可提供该贡献；第三方代码请保留来源、许可与 NOTICE。

先检索已有 issue，再用问题模板说明实际版本、操作系统、客户端、复现步骤和预期结果。附上 `cad2gis doctor --deep --profile full --json` 的相关状态、source SHA-256 与 run 状态；先移除账号、密钥、本地个人路径和敏感内容。只提交合成或获准公开的最小复现，不要上传客户原图。

```shell
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[agent,test]'
.venv/bin/python -m pytest tests/test_plugin_contracts.py tests/test_release_architecture.py
```

涉及转换时增加相关 reader、来源绑定、验收门禁测试。涉及旁路时验证源文件未改变、编辑副本的哈希绑定、路径授权、失败关闭和独立运行时。可选真实运行时验收见 [CAD_BUNDLES](docs/CAD_BUNDLES.md)。

PR 描述具体触发条件、修改后的行为、运行的检查与限制。保留 `CONDITIONAL` 和绝对精度边界，不用视觉重合替代独立检查点。
