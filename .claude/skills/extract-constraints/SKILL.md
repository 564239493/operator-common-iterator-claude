---
description: 从算子 Markdown 提取符合生成器模型的 constraints.json，供 constraint-extractor 使用。
---

# 约束提取规范

输入必须包含：算子文档、当前提示词、当前轮目录。

1. 逐节阅读文档，区分明确约束、示例和说明性文字。
2. 按当前提示词要求输出完整 JSON，不在 JSON 外夹带解释。
3. `operator_name` 必须与文档一致；平台、dtype、format、shape、取值范围和跨参数
   约束必须可追溯到原文。
4. 写入 `<iter-dir>/constraints.json`。
5. 执行：
   `python scripts/validate_artifacts.py constraints <iter-dir>/constraints.json`
6. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因。

