# 完整阶段哈希的有界编码优化

`stage_contract.py` 仍计算原合同要求的完整状态 SHA-256；没有启用阶段缓存、
跳过实体、缩小输入范围或改变 canonical JSON。优化把可估计大小的小片段交给
公共 `JSONEncoder.encode()`，以 64 KiB 预算批量编码；超出预算的容器继续分块。
循环引用保持拒绝，重复但非循环的引用仍可编码。

本轮使用 Lamteh main 的真实 reader records 第 0–4,999 条；该连续片段
13,427,159 bytes，SHA-256 为
`3b02c2ac3c3a3adeff26833504c5f3421c6fa955227f78fec716940ecdd9bb19`。
基线与候选分别执行五轮，比较完整哈希和墙钟时间中位数。

| 输入表示 | 原实现中位秒 | 新实现中位秒 | 此哈希阶段加速 |
|---|---:|---:|---:|
| 原始 reader records | 0.9673 | 0.7581 | 1.28× |
| Source entity dataclasses | 1.1301 | 0.5633 | 2.01× |
| Entity lookup | 1.4277 | 0.6078 | 2.35× |

所有比较的 canonical SHA-256 完全相同。72 项相关测试通过，覆盖 Unicode、
非字符串键、dataclass、集合、循环、异常值、字节一致性和分块内存边界。
长序列不再需要一次性编码全部内容；巨大字典仍必须按原合同排序键，
因此仍占用与键数量有关的空间。

加速比只代表这次样本的哈希阶段，不代表完整 DWG 转换速度，也不能证明大型
工程的内存问题已全部解决。原始计时、CPU 时间、tracemalloc 开销和峰值分别保留在
验证包的 `performance/cad2gis-stage-hash-benchmark-cpu.json` 与
`performance/cad2gis-stage-hash-benchmark.json`；可复现实验脚本一并提供。
