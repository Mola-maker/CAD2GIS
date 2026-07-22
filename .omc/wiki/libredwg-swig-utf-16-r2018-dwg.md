---
title: "LibreDWG SWIG UTF-16 文本截断（R2018 DWG）根因与修复"
tags: ["libredwg", "swig", "utf-16", "dwg", "text-truncation", "converter", "R2018"]
created: 2026-07-17T10:13:10.715Z
updated: 2026-07-17T10:13:10.715Z
sources: []
links: []
category: debugging
confidence: medium
schemaVersion: 1
---

# LibreDWG SWIG UTF-16 文本截断（R2018 DWG）根因与修复

# LibreDWG SWIG 读 R2018 DWG 文本被截断为单字符

## 症状
- TEXT/MTEXT/ATTRIB 的文本经 SWIG 字段（如 `entity.tio.TEXT.text_value`）读出只剩首字符（"DMPH-1.010.C09"→"D"，"123"→"1"）
- 导致标注挂接/分类中所有基于文本的逻辑静默失效（真标签 0 命中、Tier-2 关键词零命中、annotation_text 全是单字符噪声）

## 根因
Hutabohu 等 R2018（version 40）DWG 的字符串以 UTF-16 (TU) 存储；SWIG 绑定把字段当 C 字符串处理，在首个 NUL 字节截断——UTF-16 的 ASCII 字符每隔一字节就是 NUL。

## 修复
用 libredwg.so 导出的 `dwg_dynapi_entity_utf8text(struct_ptr, entity_name, field_name)` 做 TU→UTF-8 转换（ctypes 桥）。converter.py 中的通用助手：`_entity_utf8_text(struct_ptr, entity_name, field_name)`（2026-07-17 A 组件修复时加入）。

## 安全通道
- `dwgread -O json` CLI 输出的文本是正确解码的全串（layout_miner.py 走此通道，安全）
- 经修复后的 converter 写出的 gpkg 文本字段为全串

## 教训
凡新增经 SWIG 读取 DWG 字符串字段的代码，必须走 dynapi utf8text 桥或 dwgread JSON 通道；见到"单字符文本"症状先想到本条。

