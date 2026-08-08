---
description: 扫描算子文档提取全部场景(开放类目)+设备分组，量化场景标 quant_mode/width，产 inputs/scene_scan.json v2 供 scene-scanner 使用。
---

# 场景扫描规范

输入必须包含：算子文档快照（`inputs/<doc>.md`，只读）、工作提示词
（`prompts/scan_scenes.md`，含 op-scene 规则段）、当前 run 的 `inputs/` 目录（只写
`inputs/scene_scan.json`，不碰其他文件）。

> 与 constraint-extractor 职责严格区分：你只提取"文档明确以'场景'表述的全部场景"并按设备分组，
> **不**提取参数 dtype/format/shape、不写 `constraints_in_parameters`、
> 不下 presence 依赖。constraint-extractor 后续会按你给的场景清单做屏蔽式提取。

1. Read `prompts/scan_scenes.md`，按其 **op-scene 规则段**（设备类型划分表、场景类目目录、
   提取要求）逐节提取文档全部场景；一场景一条；过滤 ACLNN_ERR_*/校验场景；类目开放。
2. 对 `category="量化场景"` 的 scene，按 §2 量化参数信号表与位宽表填充
   `quant_mode`/`quant_width`（标注已提取 scene，**非补造**）。
3. 文档无任何"场景"表述 → `has_scenarios=false`，其余留空；**不**凭算子名或参数名臆造场景。
4. 若未提取到量化场景但文档含量化参数信号 → `scan_notes` 写 warning，不补造、不置 `has_scenarios`。
5. 输出 `inputs/scene_scan.json`（schema_version=2，schema 与字段语义见 `prompts/scan_scenes.md` §4）；
   只写 JSON，不在文件外夹带解释。
6. 执行：`python scripts/validate_artifacts.py scene_scan inputs/scene_scan.json`
7. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因（不静默放过）。

边界与统一命名（写入 JSON 必须用左列标准名，仅约束量化场景的 `quant_mode`/`quant_width`）：

| 维度 | 标准取值 |
|---|---|
| 量化方式 quant_mode | `非量化` / `量化` / `伪量化`（文档原文"全量化"统一归到 `量化`；静态/动态**不**作为 mode 子类，仅在 `evidence.src_text` 注记） |
| 量化位宽 quant_width | `A8W8` / `A4W4` / `A16W8` / `A16W4` / `A8W4` / `FP8_E5M2` / `FP8_E4M3FN` / `HIFLOAT8` / `FP8_E8M0` 等，按文档实际列出 |
| 非量化 | `quant_width=null` |
| 设备组名 | 按 `prompts/scan_scenes.md` op-scene 规则段「设备类型划分」表 |

不臆造：文档未以"场景"字样明列的不进 `scenes[]`；纯算子名推断（如见
`AscendAntiQuant` 算子名就臆断有伪量化）**不**算 `evidence.src_text` 依据，
必须找到正文/表格原文。
