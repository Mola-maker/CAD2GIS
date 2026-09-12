# LibreDWG MTEXT 不稳定写出的诊断

真实 Darat、Manado 图纸的五条 MTEXT 差异，已定位为 LibreDWG 0.14 DXF 写出阶段追加了内存中的残留文字。与同一 DWG 的独立 `dwgread -O JSON` 字段逐条对照，旧读与新读均保留完整正文；变化部分位于正文之后。这不是 SVG 删除正文，也不是 Python 漏接 DXF 文本分片。

## 可核验的根因

检查官方 0.14 源包的 `src/out_dxf.c`：`cquote()` 从第 1315 行开始，原第 1373 行只在分配缓冲区的最后一个字节写结束符。调用者按原文长度的两倍加一分配空间，转义换行后，实际写入的末尾往往早于缓冲区末尾。后续 `strlen()` 因此把两者之间的未初始化内容也当成文字。

固定输入 `MTEXT\nOK`、以 `X` 预填缓冲区的独立 C 复现，原函数输出 `MTEXT^JOKXXXXXXX`，修复后输出 `MTEXT^JOK`。额外八个用例覆盖空串、ASCII、LF、CR、连续换行、尾部换行及 UTF-8 字节，均通过。补丁只在实际写入位置补结束符，保留边界分支，不改变内存分配、释放或返回指针。

| 输入或证据 | SHA-256 |
| --- | --- |
| 官方 `libredwg-0.14.tar.gz` | `cb6ee0b078c6d9e0f09d66f1feac33ba6342df88ae544e9f9335fab475218351` |
| 已复现缺陷的 Windows `dwg2dxf.exe` | `955f5f2855bc0c5a8bd7a93424ea259ab14405799638b889b2b3c5918c529e3e` |
| 实测配套 `libredwg-0.dll` | `cc86e25e755ac225eadc839eaa232c2f2233c7a1b60fcd77b2cd1915761aef12` |
| 本次最小结束符补丁 | `e47037ab526f7d5adadcdadda34153764aedcbdc76f970a65137cd4d7f32ad1b` |
| 本地 C 复现原稿 | `d8041b1352a7fb214584693993882437c775b196c5c269b3dd40c71704891c38` |

官方源包地址：[LibreDWG 0.14 源代码](https://github.com/LibreDWG/libredwg/releases/download/0.14/libredwg-0.14.tar.gz)。原稿和补丁位于工作树 `tmp/reader-investigation/`；打包会话负责把采用的补丁、许可证、完整对应源码和构建记录加入原生运行时交付。最终构建应以交付中的逐文件哈希为准。

Windows EXE 会加载原生 DLL，因此环境诊断绑定上述两项哈希的组合。更换 DLL 后不会仅因 EXE 相同而误报；未知组合保留“未评估”。选用已知缺陷组合的 doctor/MCP 状态为 `limited`，其他读取器不受影响。见 [运行时诊断规则](NATIVE_READER_DIAGNOSTICS_2026-09-11.md)。

## 真实图纸重复性证据

同一原件和同一 Windows 二进制，分别启动三个独立转换进程：

- Manado 的 `74F6` 原生 DXF 文本长度为 237、242、237，其中一次多出五个字符。全部 330 条 MTEXT 的 JSON 原文字典在三次读取中相同。
- Darat 两条 MTEXT 的有效 DXF 正文长度均为 374；某轮输出达到 732/730。多余内容可以在同一 DWG 的另一个 MTEXT 对象 `2670B` 中找到。全部 49 条 JSON 原文在三次读取中相同。
- 原生进程退出码均为零，因此“进程成功退出”不足以证明文字完整性。
- DXF group 3 与 group 1 拼接结果和 ezdxf 读出的字符串相同，排除了 Python 读分片缺失。

另有 DXF 编码声明与度数符号字节不一致的问题。本次 reader 原有的可逆转义保留了该证据；没有猜测编码或静默清洗文字，也不把它与堆残留问题混为一谈。

完整本地调查为 `tmp/reader-investigation/READER_MTEXT_FINDINGS_20260911.md`，原始重复读取和字段对照 JSON 位于同目录。含原件文字的输出不进入源码归档。

## 修复与原标准工程的关系

此缺陷说明旧读文本较长可能包含错误尾部，不能把旧字符串长度当作完整性标准。修复后的新事实仍须与既有语义批准的清单绑定规则核对。旧批准哈希不因算法修复自动获得新事实授权。

Git baseline、人工 GCP、原件和封存事实均未修改。Darat 的历史源事实重放证明了既有工程可重复；Manado 当前缺少与最新批准哈希精确匹配的完整快照，继续保留绑定阻断。数值和源绑定细节见 [九图回归](CORPUS_UPGRADE_2026-09-11.md)。

最小 C 修复验证不等于完整原生运行时验收。Windows/Linux 重建后应再次进行真实图纸多轮重复性、JSON 原文对照及 ATTRIB/SVG 原生流程检查，并记录实际二进制哈希；这些结果由独立的平台交付记录提供。

## 2026-09-12 原生重建复测

Windows 与 Linux 补丁版均已完成完整编译及 Darat/Manado 各五次独立 JSON/DXF 进程复测。所有 49/330 条 MTEXT 与 JSON 原文对照通过，所有 JSON 对象与 DXF 记录事实在各平台内重复稳定，原件哈希不变。比较只采用明确的 LF/CR DXF 转义与 B0 度数解码，不裁剪任意文字。

Windows `dwg2dxf.exe` SHA256 为 `966125d62564fc04d310c541e4ff1f8732fd9088739cf8ed2bd62427c8c67122`；Linux `dwg2dxf` 为 `b99456cbff200d72386ea263a772799016a26d710d5366cf9ccb1850212a79f9`。构建与验收记录分别位于 `tmp/packaging-native-patched/{windows,linux}/libredwg/{build-receipt.json,acceptance.json}`，包含对应源码和补丁来源。最终安装包的完整验收仍以平台交付报告为准。
