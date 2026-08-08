# 场景扫描提示词（scene-scanner 工作提示词）

> 本文件是 scene-scanner Agent 的工作提示词。输入算子文档快照（`inputs/<doc>.md`，
> 只读），提取该文档**明确以"场景"表述的全部场景**（开放类目），**先按设备类型划分、
> 再在每个设备类型下列出场景**，对量化场景额外标注 `quant_mode`/`quant_width`，
> 产出 `inputs/scene_scan.json`（schema_version=2），供主协调器向用户征询"设备类型→
> 逐设备场景"两级多选、再由 `scripts/render_scene_directive.py` 渲染
> `inputs/scene_directive.md` 指导 constraint-extractor 做屏蔽式提取。
>
> 本文件下文「op-scene 规则段」（delimited）是场景**提取规则**的唯一真源，
> 抄自外部 `D:\download\op_test_scene.md`；后续该外部规则变更只替换两标记之间的内容。

## 1. 任务边界

- **提取全部场景**：文档明确出现"场景"字样的内容，一场景一条，按设备类型两级组织。
  提取规则、设备类型划分表、场景类目目录、提取要求见下文「op-scene 规则段」。
- **不做约束提取**：不写参数 dtype/format/shape、不写 `constraints_in_parameters`、
  不下 presence 依赖——那是 constraint-extractor 的职责。
- **不臆造**：文档未以"场景"字样明列的不进结果；纯算子名 / 参数名推断不算依据，必须摘
  正文或表格原文作 `evidence.src_text`。
- **只读文档快照**，只写 `inputs/scene_scan.json`，不碰其他文件。

## 2. 量化场景分类参考表（标注 quant_mode/quant_width，非补造）

op-scene 提取出的 `category="量化场景"` 的 scene，须按下表填充 `quant_mode` ∈
{非量化, 量化, 伪量化} 与 `quant_width`；这是对**已提取** scene 的分类标注，**非补造**。
（位宽表述：`A8W8` / `A4W4` / `A16W8` / `A16W4` / `A8W4` / `FP8_E5M2` / `FP8_E4M3FN` /
`HIFLOAT8` / `FP8_E8M0` 等，按文档实际列出；非量化 `width=null`。）

| 量化方式 | 隐式信号（Optional 参数在场组合） | 显式信号（若有） |
|---|---|---|
| 非量化 | scale/offset/antiquant/perTokenScale 类参数全 None | `quantMode=0` / "不量化" |
| 伪量化 (antiquant) | `antiquantScaleOptional` + `antiquantOffsetOptional` 在场（weight 整型、x 浮点） | `antiquantMode` 枚举、"伪量化/反量化" |
| 量化 | `scaleOptional` + `offsetOptional` 在场（x 与 weight 均整型）；`perTokenScaleOptional` 在场为动态路径，仅 evidence 注记，不另立 mode | 文档"全量化/静态量化/动态量化"原文用词，统一归 `mode=量化` |

> 若文档以场景分类表组织（如 GroupedMatmulV5），直接按表行枚举，比按参数推断更可靠。

## 3. 兜底 warn（不补造）

若 op-scene 提取**未得到任何 `category="量化场景"` 的 scene**，但文档含量化参数信号（上表
隐式信号命中，或 `quantMode`/`gmmXQuantMode`/位宽表达式等），则在 `scene_scan.json` 的
`scan_notes` 写一条 warning：`{"kind":"quant_signal_no_scene","message":"文档含量化参数信号
<列表> 但 op-scene 未提取到量化场景，可能遗漏剪枝"}`。**不补造 scene、不置 `has_scenarios`、
不填 quant 字段**。相较 v1 这是可接受回归（宁可放过少数算子剪枝，也不臆造场景）。

## 4. 输出 JSON schema（v2）

```json
{
  "schema_version": 2,
  "has_scenarios": true,
  "has_quant_scenarios": true,
  "device_types": ["通用", "Atlas A2 训练/推理系列"],
  "scenes": [
    {
      "id": "s1", "name": "非量化", "category": "量化场景",
      "description": "x为FLOAT32，weight为FLOAT32",
      "device_types": ["通用"],
      "quant_mode": "非量化", "quant_width": null,
      "evidence": { "src_text": "原文摘录..." }
    },
    { "id": "s2", "name": "全量化-A8W8", "category": "量化场景",
      "description": "x为INT8，weight为INT8", "device_types": ["通用"],
      "quant_mode": "量化", "quant_width": "A8W8",
      "evidence": { "src_text": "场景分类表第 N 行原文..." } },
    { "id": "s3", "name": "TND", "category": "TND场景",
      "description": "TND 布局下 key/value 分离输入...",
      "device_types": ["通用", "Atlas A2 训练/推理系列"],
      "quant_mode": null, "quant_width": null,
      "evidence": { "src_text": "原文..." } }
  ],
  "quant_modes": ["非量化", "量化"],
  "quant_widths_by_mode": { "非量化": [], "量化": ["A8W8"] },
  "valid_combos": [{"mode":"非量化","width":null},{"mode":"量化","width":"A8W8"}],
  "scan_notes": []
}
```

字段语义：

- `schema_version` (int, 必填=2)：v2 标识。validator/render 见缺失按 v1 跑老规则。
- `has_scenarios` (bool, 必填)：= `scenes` 非空（参数信号不置 true，只写 `scan_notes`）。
  false → 其余字段留空。
- `has_quant_scenarios` (bool)：是否存在 `category="量化场景"` 的 scene。
- `device_types` (list[str])：文档涉及的全部设备组，按 通用→350→A2→A3→推理系列→训练系列→200I/500→其他 顺序。
- `scenes[]` (list[dict])：每条 `id`（文件内唯一）、`name`、`category`（开放类目，列表外用原文措辞）、
  `description`（保留数值/类型/范围）、`device_types`（⊂ 顶层，可多设备）、
  `quant_mode`/`quant_width`（**仅量化场景置值**，非量化/非量化类目 scene 为 null）、
  `evidence.src_text`（非空原文摘录，非算子名推断）。
- `quant_modes`/`quant_widths_by_mode`/`valid_combos`：派生字段（兼容老路径），由 `scenes[]`
  中 `quant_mode` 非空的条目重算；scanner 直接产出、validator 重算校验一致性。
- `scan_notes` (list[{kind,message}], 可选)：兜底 warning；`kind="quant_signal_no_scene"` 时 warn 通过。

## 5. 边界情形

- **文档无任何场景**（op-scene "文档中未提及任何场景"）→ `has_scenarios=false`，其余留空。
- **纯非量化算子**：若有量化场景则照常列；若文档无任何"场景"表述 → `has_scenarios=false`。
- **场景同属多设备**（如 A2+A3 合并标注）→ `device_types` 列多个，在各相关设备组下都列出。
- **量化场景的 quant_mode/quant_width**：按 §2 表标注；文档只区分方式不细分位宽 → `width=null`。
- **FP8 系**：按文档实际列出的 FP8 子型分列，不合并成"FP8系"占位符。

## 6. 自校验

产出后必须跑：

```bash
python scripts/validate_artifacts.py scene_scan inputs/scene_scan.json
```

校验项（见 `validate_artifacts.py: validate_scene_scan`）：
- `schema_version=2`；`has_scenarios` 必填且为 bool。
- `has_scenarios=true` 时 `device_types` 非空 list[str]、`scenes` 非空 list[dict]；
  每条 `id` 唯一、`evidence.src_text` 非空、`device_types` ⊂ 顶层。
- 每条 `quant_mode` 非空的 scene 须有非空 `evidence.src_text`（替代老"每 combo 有 evidence"）。
- 派生一致性：`has_quant_scenarios`/`quant_modes`/`quant_widths_by_mode`/`valid_combos` 从 `scenes` 重算与声明值一致。

失败则据错误修正，最多三次；仍失败则返回阻断原因，不静默放过。

---

<!-- BEGIN op-scene rules（来源: D:\download\op_test_scene.md | 复制日期 2026-08-08 | 后续规则变更只替换本标记间内容） -->

# 算子文档场景提取（op-scene 规则）

## 适用场景

用户提供一个**单个算子 API 文档**（aclnn 系列算子文档，.md 文件路径或直接粘贴内容），需要从文档**全文**中提取文档明确提到的**场景**，**先按设备类型划分，再在每个设备类型下列出场景**，整理成结构化输出。

## 输入

- 文档文件路径（如 `ops-nn/aclnnMatmul.md`），或直接粘贴的文档内容
- 若用户给出目录/多个文档：逐个文档独立分析，每个文档单独成节

## 分析步骤

1. **通读全文**：阅读文档和约束相关的小节——产品支持情况、函数原型、参数说明、返回值错误场景表、约束说明、调用示例等，找出所有**明确出现"场景"字样**的内容（如"XX场景"、"XX场景下"、"在XX场景中"）。
2. **一条场景 = 一个条目**：文档中提到的一个场景（如"稀疏计算场景"、"TND场景"、"量化场景"、"prefix稀疏计算场景"、"band场景"等）提取为一条；文档对该场景的说明文字（触发条件、参数取值要求、数值范围等）**概括进该条目的描述中**，但**不再按参数取值分支把一条场景拆成多条**。
3. **过滤掉干扰信息**：并不是文档中只要提到了**场景就要提取出该场景，必须要不仅提到了，而且后文还针对这个算子提出了该场景下各个参数的取值约束，那么才认为这个算子是有这个场景的测试需求
4. **不提取错误/校验场景**：返回值错误场景表中的 ACLNN_ERR_*（161001/161002/561002/561103/361001 等）报错内容、参数校验失败的报错说明，一律不提取。
5. **识别设备类型标注**：按下表将场景划分到设备组；没有设备标注的内容归入"通用"。
6. **整理输出**：按"设备类型 → 场景"两级组织。

## 设备类型划分

| 文档原文标注 | 输出组名 | 说明 |
|---|---|---|
| （无任何设备标注） | **通用** | 适用所有设备 |
| Atlas 350 加速卡 | **Atlas 350 加速卡** | 简写 350 |
| Atlas A2 训练系列产品 / Atlas A2 推理系列产品 | **Atlas A2 训练/推理系列** | 成对出现时合并为一组，简写 A2 |
| Atlas A3 训练系列产品 / Atlas A3 推理系列产品 | **Atlas A3 训练/推理系列** | 同上，简写 A3 |
| Atlas 推理系列产品（未标注 A2/A3） | **Atlas 推理系列产品** | 推理卡场景归此组 |
| Atlas 训练系列产品（未标注 A2/A3） | **Atlas 训练系列产品** | |
| Atlas 200I/500 A2 推理产品 | **Atlas 200I/500 A2 推理产品** | |
| 标准列表之外的设备名（如 "Atlas 推理系列加速卡产品"、"Ascend950 系列平台"） | 按原文归组 | 组名注明"（原文设备名）" |

规则：
- 一条场景同时适用于多个设备（如 "Atlas 350 加速卡、Atlas 200I/500 A2 推理产品不支持XX"）→ 在每个相关设备组下都列出
- 文档中设备标注与 A2/A3 成对出现时（如 "Atlas A2 训练系列产品/Atlas A2 推理系列产品、Atlas A3 训练系列产品/Atlas A3 推理系列产品"）→ 分别归入 A2 组与 A3 组
- 某个设备组无内容时省略不写

## 场景类型

每个条目标注类型，类型只用于归类，**不改变提取粒度**（一条场景始终一条）：
1. **量化场景**：文档明确以量化场景划分，比如量化、非量化、伪量化、全量化、反量化。
2. **卷积场景**：conv1d、conv2d、conv3d
3. **专家场景**：有无专家
4. **band场景**
5. **外切场景**
6. **TND、NTD、TND_NTD、NTD_TND**
7. **BNSD_BSND、BSH_BNSD、BSND_BNSD、BSH_NBSD、BSND_NBSD、BNSD_NBSD**
8. **Key/Value分离场景**：凡文档提到 key/value 分离相关（key/value 分开输入或存储、key/value 量化/反量化参数分离，如 KV 伪量化参数分离、keyAntiquantScale/valueAntiquantScale 等）都归入此类。
9. **MASK**
10. **MLA**

补充规则：
- **类目不封闭**：文档中出现但不在上表里的场景类别→ **新增类目**，类型标注直接使用文档原文场景名（如 `场景 3（稀疏计算场景）`）。
- **布局类条目粒度**：按文档原文组织——文档分开讲的布局各成一条（标注各自场景名，如 `（TND场景）`、`（NTD_TND场景）`），文档合并讲的合并为一条。

（**错误/校验场景不提取**——返回码报错、参数校验失败的描述一律跳过。）

## 输出格式

```
## <算子名>

**通用**:
- 场景 <编号>（**场景）: <内容，保留原文关键信息（数值/类型/范围）>

**Atlas A2 训练/推理系列**:
- 场景 <编号>（**场景）: ...

**Atlas 350 加速卡**:
- 场景 <编号>（**场景）: ...

（设备组按 通用 → Atlas 350 加速卡 → Atlas A2 → Atlas A3 → Atlas 推理系列产品 → Atlas 训练系列产品 → Atlas 200I/500 A2 → 其他 顺序排列；无内容的组省略）
```

场景编号在每个设备组内从 1 递增。`（**场景）` 中的 ** 为场景类目名（如 量化、卷积、band、TND、稀疏计算 等），实际输出标注为 `（<类目名>场景）`。

> 本项目落地注记：上述 Markdown 输出格式是 op-scene 原始形态；在本项目中 scene-scanner 须把提取结果落到 §4 的 v2 JSON schema（`scenes[]` 每条含 `id`/`name`/`category`/`description`/`device_types`/`quant_mode`/`quant_width`/`evidence.src_text`），而非 Markdown。`category` 取本节类目名（如"量化场景"/"TND场景"）。

## 提取要求

- **只提取文档中明确出现"场景"字样的内容**；不以"场景"表述的参数取值分支（如"sparseMode 为 0 时……"、"X 的数据类型为 INT8 时……"单独一条）**不提取**
- **错误/校验场景不提取**：ACLNN_ERR_* 返回码、参数校验失败的报错描述一律跳过
- **一条场景 = 一个条目**：文档对一个场景的全部说明（触发条件、参数取值、范围）概括在一条里，不按取值分支拆分
- 只提炼文档原文中出现的内容：不增补、不推断、不改写语义；数值、类型、范围等关键信息必须保留
- 所有小节都覆盖：产品支持情况表的设备差异、参数说明表中的 "使用说明" 列、约束说明等都要纳入（仅提取其中明确以"场景"表述的内容）
- 场景命名优先使用文档原文措辞；原文没有名字的用一句话概括
- 文档某设备组只有单一场景时也照常列出，不省略
- 若文档整体没有任何场景描述，输出 "文档中未提及任何场景"
- 同一算子的同一条场景可能同时属于多个设备组（如 A2+A3 合并标注），需在各组分别列出

## 示例

输入：`aclnnGroupedMatmulV5.md` 中关于 sparse 模式与量化的描述
输出：

```
## aclnnGroupedMatmulV5

**通用**:
- 场景 1（量化场景）: 非量化 x为FLOAT32，weight为FLOAT32
- 场景 2（量化场景）: 全量化-A8W8 x为INT8，weight为INT8
- 场景 3（量化场景）: 伪量化-A8W4 x为INT8，weight为INT4（仅支持x、weight、y均为单tensor的场景）
```

（示例中：量化、非量化、全量化、伪量化均归"量化场景"类目；"稀疏计算场景"等列表外的场景类别按文档原文新增类目标注；不列 161001/161002 等错误场景；不列"当X为INT8时Y必须取…"这类无"场景"字样的取值分支；不标旧类型与【场景参数】）

<!-- END op-scene rules -->
