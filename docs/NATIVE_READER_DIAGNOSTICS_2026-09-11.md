# 原生 MTEXT 读取诊断

doctor、MCP `get_runtime_status` 和 `debug_mcp(scope="runtime")` 现在识别一个已实测存在缺陷的 Windows `dwg2dxf.exe`：

`955f5f2855bc0c5a8bd7a93424ea259ab14405799638b889b2b3c5918c529e3e`

该 executable 的 PE 导入表引用相邻 `libredwg-0.dll`，实际缺陷实现位于该运行库。本机实测库 SHA-256 为 `cc86e25e755ac225eadc839eaa232c2f2233c7a1b60fcd77b2cd1915761aef12`。因此已知缺陷结论要求 **exe 与相邻 DLL 两个哈希同时精确匹配**，避免把仅替换了 DLL 的构建误判为仍有原缺陷。

此二进制的原生 `cquote` 没有在实际输出末尾设置终止符，可在 MTEXT 后追加堆中残留文本；同一原件、同一 executable、多次进程退出码都为零时，输出尾部仍可能变化。Darat/Manado 两份真实 DWG 的逐对象对比、3 次独立原生读取及从原代码提取的 C 复现共同支持该结论。原始源码归档 SHA-256 为 `cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351`；这是证据来源标识，不是所有相同版本二进制的缺陷判定规则。

诊断只读取实际发现的 executable 和匹配时相邻 DLL 的字节，不运行二进制、不打开图纸，且不缓存指纹。`portable_runtime.libredwg.text_fidelity`（MCP 为 `libredwg.text_fidelity`）包含真实 `executable_sha256`、`runtime_library_sha256`、状态、缺陷编号、判断范围和处理建议：

| 条件 | 状态 | 就绪状态的影响 |
|---|---|---|
| exe 与 DLL 的 SHA 均精确匹配上述实测组合 | `known_defect` / `LIBREDWG_CQUOTE_MTEXT_TAIL` | 实际选用 CLI 时 doctor/MCP 为 `limited`；选用 bindings 或 AutoCAD 不受未使用 CLI 的影响 |
| exe 已知但相邻 DLL 缺失、不可读或不同 | `not_evaluated` / `runtime_library_not_evaluated` | 不凭 exe 判断运行库已修复或仍损坏；提示核验实际加载库与来源 |
| 其他哈希，含不同 Linux 构建或新修补构建 | `not_evaluated` | 不认证修复，也不因版本相同直接判坏；保留原能力就绪检查 |
| 无 CLI | `not_available` | 缺失能力由原读取器选择检查处理 |
| 文件不可读、超出指纹读取预算或读取过程中变化 | `not_evaluated` / `fingerprint_unavailable` | 不抛异常使诊断崩溃，不伪造哈希 |

所有状态都保留 `text_fidelity_verified=false`、`fixed_build_verified=false`。这里没有接收任意本地“已修复”声明或 sidecar 白名单；正式修补构建的 source/patch/executable 来源与实际图纸回归应在平台构建证据中单独记录。新二进制哈希不同只意味着不再命中这个已知二进制，不能单独证明修补有效。

遇到 `known_defect` 时，应保留原 DWG、源快照与 baseline hash；在验证替代原生构建的补丁来源、原始文本和多次读取一致性后，通过 `CAD2GIS_LIBREDWG_CLI` 选择相应 executable，并在新输出目录重新检查。不应反复安装同一哈希、重写批准 inventory hash 或拼接多个读取轮次文本来消除诊断。

此次仅改环境状态报告，不改 source records、inventory、profile、baseline、reader 选择或转换内容。已绑定快照的可重复重放并不能证明其中历史文本尾部准确。完整本机研究证据在 `tmp/reader-investigation/READER_MTEXT_FINDINGS_20260911.md`；原始工程文本和原件不纳入源码交付。

验证包括实际 exe/DLL 哈希复算、真实 doctor 与 MCP 均返回 `limited`，记录于 `E:/branch_CAD2GIS/validation/semantic-upgrade-20260911/native-diagnostics-known-pair.json`。定向测试覆盖精确匹配、exe 或 DLL 替换、未知 Windows/Linux 构建、缺失/不可读/超预算/读取中变化、CLI 与其他读取器选择、MCP/debug_mcp 暴露及现有无新字段测试夹具兼容性。Redis 检查和其余环境模块保持原行为。
