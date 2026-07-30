# 场景扫描提示词（scene-scanner 工作提示词）

> 本文件是 scene-scanner Agent 的工作提示词。输入算子文档快照（`inputs/<doc>.md`，
> 只读），枚举该文档**实际涉及**的「量化方式 × 量化位宽组合」场景，产出
> `inputs/scene_scan.json`，供主协调器向用户征询场景选择、再由
> `scripts/render_scene_directive.py` 渲染 `inputs/scene_directive.md` 指导
> constraint-extractor 做屏蔽式提取。

## 1. 任务边界

- **只做场景枚举**：列出文档涉及的 (量化方式, 位宽) 组合。
- **不做约束提取**：不写参数 dtype/format/shape、不写 `constraints_in_parameters`、
  不下 presence 依赖——那是 constraint-extractor 的职责。
- **不臆造**：文档未明列的场景不进结果；纯算子名 / 参数名推断不算依据，必须摘
  正文或表格原文作 `evidence.src_text`。
- **只读文档快照**，只写 `inputs/scene_scan.json`，不碰其他文件。

## 2. 量化方式识别

量化方式通常不是显式参数（如 GroupedMatmulV5 无 `quantMode` 参数），而是由一组
Optional 参数的在场组合隐式表达。按下表识别：

| 量化方式 | 隐式信号（Optional 参数在场组合） | 显式信号（若有） |
|---|---|---|
| 非量化 | scale/offset/antiquant/perTokenScale 类参数全 None | `quantMode=0` / "不量化" |
| 伪量化 (antiquant) | `antiquantScaleOptional` + `antiquantOffsetOptional` 在场（weight 整型、x 浮点） | `antiquantMode` 枚举、`quantMode` 对应值、"伪量化/反量化" |
| 量化 | `scaleOptional` + `offsetOptional` 在场（x 与 weight 均整型）；`perTokenScaleOptional` 在场为动态路径，仅作 evidence 注记，不另立 mode | 文档"全量化"/"静态量化"/"动态量化"原文用词，统一归 `mode=量化` |

> 若文档以场景分类表组织（如 GroupedMatmulV5），直接按表行枚举，比按参数推断更可靠。

## 3. 量化位宽组合识别

位宽组合描述 x 与 weight 的量化位宽配对。常见取值：

| 位宽组合 | 含义 |
|---|---|
| `A8W8` | x=INT8, weight=INT8（量化） |
| `A4W4` | x=INT4, weight=INT4（量化） |
| `A16W8` | x=FLOAT16, weight=INT8（伪量化） |
| `A16W4` | x=FLOAT16, weight=INT4（伪量化） |
| `A8W4` | x=INT8, weight=INT4（伪量化） |
| `FP8_E5M2` / `FP8_E4M3FN` / `HIFLOAT8` | weight 或 x·weight 用 FP8 系（伪量化或量化） |
| `FP8_E8M0` | 仅作 MX 量化的 scale 类型 |

非量化方式无位宽，`width=null`。

## 4. 输出 JSON schema

```json
{
  "has_quant_scenarios": true,
  "quant_modes": ["非量化", "量化", "伪量化"],
  "quant_widths_by_mode": {
    "非量化": [],
    "伪量化": ["A16W8", "A16W4", "A8W4"],
    "量化": ["A8W8", "A4W4"]
  },
  "valid_combos": [
    {"mode": "非量化", "width": null},
    {"mode": "伪量化", "width": "A16W8"},
    {"mode": "量化", "width": "A8W8"}
  ],
  "evidence": [
    {"mode": "非量化", "width": null, "src_text": "原文摘录..."},
    {"mode": "量化", "width": "A8W8", "src_text": "场景分类表第 N 行原文..."}
  ]
}
```

字段语义：

- `has_quant_scenarios` (bool, 必填)：文档是否涉及任何量化场景。false 时其余字段
  留空（`quant_modes=[]`、`valid_combos=[]`、`evidence=[]`）。
- `quant_modes` (list[str])：文档涉及的全部量化方式，标准名见 §2 左列。
- `quant_widths_by_mode` (dict[str→list[str]])：每个方式下文档涉及的位宽组合；
  `非量化` 固定 `[]`。
- `valid_combos` (list[{mode, width}])：文档实际支持的 (方式, 位宽) 合法组合，
  每条 `mode` 必须在 `quant_modes` 内，`width` 必须在
  `quant_widths_by_mode[mode]` 内（`非量化` 用 `null`）。
- `evidence` (list[{mode, width, src_text}])：每条 `valid_combos` 必须有且仅有
  一条对应 (mode, width) 的 evidence，`src_text` 摘自原文（场景表行 / 约束子节 /
  参数说明原文），非空、非算子名推断。

## 5. 边界情形

- **纯非量化算子**（多数逐元素 / 归约 / 排序 / 插值算子）：`has_quant_scenarios=false`。
- **文档只区分方式不细分位宽**（如某些算子只说"支持伪量化"未列位宽配对）：
  `width=null` 记一条 (方式, null)，`quant_widths_by_mode[方式]=[]`。
- **量化的静态/动态子区分**：文档可能区分"静态量化/动态量化"，但 `mode` 维度统一为
  `量化`，**不**另立 `量化-静态`/`量化-动态`；静态/动态仅在 `evidence.src_text`
  摘原文注明。`perTokenScaleOptional` 在场即动态路径，evidence 照摘。
- **FP8 系**：按文档实际列出的 FP8 子型分列（`FP8_E5M2` 等），不合并成"FP8系"
  占位符，除非文档本身就只说"FP8 系"。

## 6. 自校验

产出后必须跑：

```bash
python scripts/validate_artifacts.py scene_scan inputs/scene_scan.json
```

校验项（见 `validate_artifacts.py: validate_scene_scan`）：
- `has_quant_scenarios` 必填且为 bool；
- `=true` 时 `quant_modes` 非空 list[str]、`quant_widths_by_mode` 为 object、
  `valid_combos` 非空；
- 每条 `valid_combos` 的 `mode` ∈ `quant_modes`、`width` ∈
  `quant_widths_by_mode[mode]`（null 除外）；
- 每条 `valid_combos` 必须有对应 (mode, width) 的 evidence 且 `src_text` 非空。

失败则据错误修正，最多三次；仍失败则返回阻断原因，不静默放过。
