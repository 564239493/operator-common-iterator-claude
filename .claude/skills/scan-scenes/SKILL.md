---
description: 扫描算子文档枚举量化方式×位宽组合场景，产 inputs/scene_scan.json 供 scene-scanner 使用。
---

# 场景扫描规范

输入必须包含：算子文档快照（`inputs/<doc>.md`，只读）、工作提示词
（`prompts/scan_scenes.md`）、当前 run 的 `inputs/` 目录（只写
`inputs/scene_scan.json`，不碰其他文件）。

> 与 constraint-extractor 职责严格区分：你只枚举"文档涉及哪些 (量化方式, 位宽)
> 场景"，**不**提取参数 dtype/format/shape、不写 `constraints_in_parameters`、
> 不下 presence 依赖。constraint-extractor 后续会按你给的场景清单做屏蔽式提取。

1. 逐节阅读文档，定位以下信号：
   - "场景分类表 / 场景矩阵 / 支持场景表 / quantMode 支持"等表格及其行；
   - 量化相关参数与枚举：`quantMode` / `gmmXQuantMode` / `x1QuantMode` /
     `antiquantMode` / `isSymmetrical` / `perTokenScale` / `scaleOptional` /
     `offsetOptional` / `antiquantScaleOptional` / `antiquantOffsetOptional` /
     `deqScale` / `smoothScalesOptional` 等；
   - 位宽表述：`A8W8` / `A4W4` / `A16W8` / `A16W4` / `A8W4` /
     `FLOAT8_E5M2` / `FLOAT8_E4M3FN` / `HIFLOAT8` / `FLOAT8_E8M0` 等；
   - `perTokenScaleOptional` 在场标记动态路径（仅作 evidence 注记，不另立量化方式 mode）。
2. 据此判定文档**实际涉及**的量化方式与位宽组合，列全 `valid_combos`。每个组合
   必须有 `evidence.src_text` 摘自原文（场景表对应行或约束子节原文，非算子名推断）。
3. 文档无任何量化场景（如纯逐元素 / 纯归约 / 纯排序算子）→
   `has_quant_scenarios=false`，其余字段留空，**不要**凭算子名或参数名臆造量化场景。
4. 输出 `inputs/scene_scan.json`，schema 与字段语义见 `prompts/scan_scenes.md`；
   只写 JSON，不在文件外夹带解释。
5. 执行：`python scripts/validate_artifacts.py scene_scan inputs/scene_scan.json`
6. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因（不静默放过）。

边界与统一命名（写入 JSON 必须用左列标准名）：

| 维度 | 标准取值 |
|---|---|
| 量化方式 | `非量化` / `量化` / `伪量化`（文档原文的"全量化"统一归到 `量化`；静态/动态**不**作为 mode 子类，仅在 `evidence.src_text` 注记） |
| 量化位宽组合 | `A8W8` / `A4W4` / `A16W8` / `A16W4` / `A8W4` / `FP8_E5M2` / `FP8_E4M3FN` / `HIFLOAT8` / `FP8_E8M0` 等，按文档实际列出 |
| 非量化 | `width=null`，`quant_widths_by_mode["非量化"]=[]` |

不臆造：文档未明列的组合不进 `valid_combos`；纯算子名推断（如见
`AscendAntiQuant` 算子名就臆断有伪量化）**不**算 `evidence.src_text` 依据，
必须找到正文/表格原文。
