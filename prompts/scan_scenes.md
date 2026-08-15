# 场景扫描提示词（scene-scanner 工作提示词）

> 本文件是 scene-scanner Agent 的工作提示词。输入算子文档快照（`<run-dir>/inputs/<doc>.md`，
> 只读），按 **设备类型 → 量化模板 → 特性参数** 三级分层提取，**不设"通用"组**（无
> 设备标注内容合并到每个具体设备组下），特性参数**只提取枚举/分档类可选项**，产出
> `<run-dir>/inputs/scene_scan.json`，供主协调器向用户分轮征询"设备类型 →
> 量化模板 → 特性参数"（Q1→Q2→Q3 三轮；`device_types` 仅 1 个时跳过 Q1）、再由 `scripts/render_scene_directive.py` 渲染
> `inputs/scene_directive.md` 指导 constraint-extractor 做屏蔽式提取。
>
> 本文件下文「op-scene 规则段」（delimited）是场景**提取规则**的唯一真源，
> 抄自外部 `D:\operator_project\op_test_scene.md`；后续该外部规则变更只替换两标记之间的内容。

## 1. 任务边界

- **三级分层提取**：设备类型 → 量化模板 → 特性参数。提取规则、设备类型划分表、特性
  参数筛选规则、提取要求见下文「op-scene 规则段」。
- **不做约束提取**：不写参数 dtype/format/shape、不写 `constraints_in_parameters`、
  不下 presence 依赖——那是 constraint-extractor 的职责。
- **不臆造**：只提炼文档原文中出现的内容，不增补、不推断、不改写语义；数值、类型、
  范围等关键信息必须保留。
- **只读文档快照**，只写调度消息明确给出的
  `<run-dir>/inputs/scene_scan.json`，不碰其他文件。不得把相对路径按仓库 cwd 解析；未收到
  `<run-dir>` 时返回阻断原因。

## 2. 量化模板命名参考（非补造）

op-scene 提取出的"量化模板"是**具体量化场景**，模板名直接编码量化方式（必要时细分到
数据类型/位宽）。这是对**已提取**模板的命名标注，**非补造**。

- 典型模板名：`非量化` / `伪量化` / `全量化` / `全量化-A8W8` / `全量化-A4W4` /
  `伪量化-A8W4` / `K-C量化` / `K-C量化（A4W4）` / `K-C动态量化（A16W8）` /
  `G-B量化` / `B-B量化` / `MX全量化` / `MX伪量化` / `T-CG量化` / `全量化-GQA` /
  `全量化-Decode MLA` / `全量化-MxFP8` 等，按文档实际用词命名。
- **分类词不作为模板**：文档中概括性分类术语（如"该算子支持 K-C 量化和 mx 量化**模式**"
  中的"量化模式"）只是上级分类名，不提取为与量化模板平级的条目；其下的通用说明、参数
  约束按归属拆分并入分类下的各真实量化模板。
- 模板定义行承载**固定条件**（固定数据类型/shape、quantMode 固定值、必传、不支持项、
  对齐要求、公共约束中的固定条件与无设备标注内容），直接引用文档原文。

> 若文档以场景分类表组织（如 GroupedMatmulV5），直接按表行枚举具体模板，比按参数推断
> 更可靠。

## 3. 兜底 warn（不补造）

若 op-scene 提取**未得到任何量化模板**，但文档含量化参数信号（`quantMode`/
`gmmXQuantMode`/`antiquantMode`/位宽表达式等），则在 `scene_scan.json` 的 `scan_notes`
写一条 warning：`{"kind":"quant_signal_no_template","message":"文档含量化参数信号 <列表>
但 op-scene 未提取到量化模板，可能遗漏剪枝"}`。**不补造模板、不置 `has_scenarios`、不
填模板字段**。宁可放过少数算子剪枝，也不臆造场景。

## 4. 输出 JSON schema（v3，三级嵌套）

```json
{
  "operator": "aclnnXxx",
  "has_scenarios": true,
  "device_types": ["Atlas 350 加速卡", "Atlas A2 训练系列产品/Atlas A2 推理系列产品"],
  "devices": [
    {
      "device": "Atlas A2 训练/推理系列",
      "templates": [
        {
          "template": "非量化",
          "definition": "query/key/value 数据类型仅支持均为FLOAT16/BFLOAT16、数据格式仅支持ND…（无设备标注内容合并到本组，下同）；本模板不支持：prefix/pse/alibi",
          "unsupported_features": ["prefix", "pse", "alibi"],
          "feature_params": [
            {
              "feature": "布局（inputLayout）",
              "params": [
                { "name": "inputLayout",
                  "values": ["BNSD","BSND","BSH","TND","NTD","BSH_BNSD","BSND_BNSD","NTD_TND","BNSD_BSND"],
                  "description": "标识输入query、key、value的数据排布格式",
                  "constraint": "", "related": "" },
                { "name": "query/key/value 数据类型",
                  "values": ["FLOAT16","BFLOAT16"],
                  "description": "公式中的输入Q/K/V",
                  "constraint": "", "related": "" }
              ]
            },
            { "feature": "Mask",
              "params": [
                { "name": "sparseMode",
                  "values": [0,1,2,3,4],
                  "description": "稀疏计算模式",
                  "constraint": "取 0/1 时 attenMask 输入维度为 2 维不支持",
                  "related": "" }
              ] }
          ]
        },
        { "template": "全量化-GQA",
          "definition": "query/key/value 为 FLOAT8_E4M3FN…（本模板不支持：公共前缀场景、pse场景…）",
          "unsupported_features": ["公共前缀场景","pse场景","alibi场景","左padding场景","tensorlist场景","rope场景","后量化场景"],
          "feature_params": [
            { "feature": "Mask",
              "params": [
                { "name": "sparseMode",
                  "values": [0,3],
                  "description": "sparse的模式",
                  "constraint": "0模式下不支持传入attenMask矩阵",
                  "related": "" }
              ] }
          ]
        }
      ]
    }
  ],
  "scan_notes": []
}
```

字段语义：

- `operator` (str, 必填)：算子名，与文档一致。
- `has_scenarios` (bool, 必填)：= 任一设备 `templates` 非空（参数信号不置 true，只写
  `scan_notes`）。false → `device_types`/`devices` 留空。
- `device_types` (list[str])：文档"产品支持情况"涉及的**全部具体设备组**（**无"通用"**），
  按 350→A2→A3→推理系列→训练系列→200I/500→其他 顺序。**逐字照抄"产品支持情况"表
  `<term>…</term>` 标签内的原文作为元素，不得简写/合并/改写**（如文档写
  `Atlas A2 训练系列产品/Atlas A2 推理系列产品` 则逐字照抄，**禁止**改成
  `Atlas A2 训练/推理系列`）——此名随后要与约束表 √ 行原文取交集，改写会导致交集落空。
- `devices[]` (list[dict])：每个设备 `device`（⊂顶层 `device_types`）、`templates[]`。
  - **"与 X 相同"的设备直接内联复制 X 的 `templates`**（不写 `same_as` 引用字段，各设备
    `templates` 自洽，消费者无需做引用解析）。
- `templates[]` (list[dict])：每条 `template`（组内唯一，量化方式名）、`definition`
  （固定条件原文，含公共约束固定条件与无设备标注内容）、`unsupported_features`
  （list[str]，"本模板不支持"列出的特性）、`feature_params`（list，无可选特性的模板为空 `[]`）。
- `feature_params[]` (list[dict])：按**特性名**分组，每条 `feature`（如"布局（inputLayout）"/
  "Mask"/"PagedAttention"/"DequantChecker（反量化）"/"转置（transposeX1/transposeX2）"）、
  `params[]`。
  - **含多参数的 bullet 拆成独立 `params[]` 条目**（如文档原文 `x: 取值：…；weight: 取值：…`
    → 两条 params，各带 `name`/`values`/`description`）。
- `params[]` (list[dict])：`name`（参数名）、`values`（**非空** list，枚举离散值或分档区间；
  取值 token 如 `"BNSD"`/`0`/`true`/`"空"`/`[-2,-1]`；复杂取值的逐值注记如
  `0 KEEP_DOTO（…）` 只保留值 token，注记并入 `description`/`constraint`）、`description`
  （文档原文参数描述，非空）、`constraint`（公共约束中该参数取值约束，可空）、`related`
  （取值牵动的其他选择型关联参数约束，可空）。
- `scan_notes` (list[{kind,message}], 可选)：兜底 warning；`kind="quant_signal_no_template"`
  时 warn 通过（不阻断）。

## 5. 边界情形

- **文档无任何场景/模板**（op-scene 提取为空）→ `has_scenarios=false`，`device_types`/
  `devices` 留空。
- **纯非量化算子**：若有量化模板则照常列；若文档无任何量化场景表述 → `has_scenarios=false`。
- **模板同属多设备**（如 A2+A3 合并标注，或 A3 内容与 A2 完全一致）→ 在每个相关设备组下
  都列出（"与 X 相同"的设备内联复制 X 的 `templates`，不省略）。
- **无可选特性的模板**：只输出 `definition` + `unsupported_features`，`feature_params=[]`。
- **特性参数只提取枚举/分档类可选项**：有选择性的参数指类似枚举的选项（如 sparseMode
  取值 0/1/2/3/4），或分多档范围需用户选择其一的（如取值支持 16~1024 与 2048~4096 两档）；
  **单个取值范围不算选择**（如 blockSize 16~1024，用户不可能只取范围内某个值），不作为
  特性参数提取；只能取唯一值的条件（"仅支持 X"/"必须为 X"/"必传"）同样不提取，归入
  `definition`。**埋藏型特性按 ≥2 取值提成 feature_params**：数据格式（ND/NZ）、转置
  （bool true/false）、TensorList 单/多，模板支持 ≥2 取值时按枚举可选项提取（bool 用
  `[true,false]`、TensorList 用 `["单","多"]`、format 用文档原文 token 如 `["ND","NZ"]`）；
  仅单值时仍归 `definition`。
- **关联参数同样只提取有选择型的**：特性参数取值牵动其他参数时，作为该条目的 `related`
  信息提取，但关联参数本身也须有可选项（枚举/分档）；固定后果不作为关联参数提取。
- **错误/校验场景不提取**：ACLNN_ERR_* 返回码、参数校验失败的报错描述一律跳过。

## 6. 自校验

产出后必须跑：

```bash
python scripts/validate_artifacts.py scene_scan <run-dir>/inputs/scene_scan.json
```

校验项（见 `validate_artifacts.py: validate_scene_scan`）：
- `has_scenarios` 必填且为 bool；`operator` 非空。
- `has_scenarios=true` 时 `device_types` 非空 list[str] 且**无"通用"**、`devices` 非空
  list[dict]；每个设备 `device`⊂顶层、`templates` 非空、`template` 组内唯一；
  `feature_params` 每条 `feature` 非空、`params` 非空、每条 `name`/`values`（非空
  list）/`description` 非空。
- 派生一致性：`device_types` == `devices[].device` 全集。
- `scan_notes` 含 `quant_signal_no_template` 时 warn 通过。

失败则据错误修正，最多三次；仍失败则返回阻断原因，不静默放过。

---

## 量化模式概念参考

> 以下量化概念抄自外部 `D:\operator_project\quant_mode_introduction.md`，作为 op-scene「量化模式推导规则」的背景知识（推导规则本身见下文 op-scene 规则段）。

### 量化介绍

量化广泛应用于深度学习模型中，特别是在推理过程中。通过量化，模型可以在硬件上更高效地运行，减少计算资源的消耗和加速推理过程，同时降低模型的存储需求。

CANN算子量化是指对神经网络中Matmul等矩阵（cube）类算子的输入Tensor从高bit到低bit转换的计算过程，同时生成对应的量化参数scale。当低bit的cube计算完成后，可通过量化参数scale将低bit数值转换回高bit数值，从而保证整体计算结果的正确性（效果与直接用高bit计算近似等价），并有效提升计算效率。

- 静态量化：使用预先确定的量化参数进行量化。推理场景下对权重weight的量化一般采用静态量化，量化算子性能会更好些。
- 动态量化：使用输入数据在线计算量化参数进行量化。推理场景下对激活activation的量化一般采用动态量化，更能适应数据的变化，精度更高；训练场景下为了提升量化精度，也一般采用动态量化。注意，动态量化因为在线生成量化参数，量化算子性能会略差些。

#### 量化模式

量化模式（又称量化粒度）是指对算子的不同输入Tensor采用不同的量化计算级别，常见的量化计算模式包括：

>说明：
>
>- m、n、k变量分别表示Tensor计算的不同轴大小。
>- 左矩阵、右矩阵分别指cube算子中用于矩阵乘法计算的两个输入Tensor，一般左矩阵代表激活activation、右矩阵代表权重weight，请用户按实际情况理解和使用。

- pertensor量化（简称T量化）：量化对象既可以是左矩阵，也可以是右矩阵，每个Tensor共用一个相同的量化参数。

  假设左矩阵shape为(m, k)，右矩阵shape为(k, n)，k为reduce轴，生成量化参数的shape为(1, )。

- perchannel量化（简称C量化）：量化对象是右矩阵，每个channel分别使用独立的量化参数。

  假设右矩阵shape为(k, n)，k为reduce轴，生成量化参数的shape为(n, )。

- pertoken量化（简称K量化）：量化对象是左矩阵，每个token分别使用独立的量化参数。

  假设左矩阵shape为(m, k)，k为reduce轴，生成量化参数的shape为(m, )。

- pergroup量化（简称G量化）：量化对象既可以是左矩阵，也可以是右矩阵，在reduce轴上对数据分组，每组使用独立的量化参数。
  - 假设左矩阵shape为(m, k)，k为reduce轴，在k轴上分组，group size为gs，生成量化参数的shape为(m, k/gs)。
  - 假设右矩阵shape为(k, n)，k为reduce轴，在k轴上分组，group size为gs，生成量化参数的shape为(k/gs, n)。

- perblock量化（简称B量化）：量化对象既可以是左矩阵，也可以是右矩阵，在所有轴上对数据分块，每块使用独立的量化参数。

  - 假设左矩阵shape为(m, k)，k为reduce轴，在m、k轴上分别按(bs, bs)块对数据分组，bs为block size，生成量化参数的shape为(m/bs, k/bs)。
  - 假设右矩阵shape为(k, n)，k为reduce轴，在k、n轴上分别按(bs, bs)块对数据分组，bs为block size，生成量化参数的shape为(k/bs, n/bs)。

#### 常见组合量化

- 全量化：一般是指对左、右矩阵均进行量化的模式，包括
  - pertensor-perchannel量化模式（简称T-C量化模式）
  - pertoken-perchannel量化模式（简称K-C量化模式）
  - pergroup-perblock量化模式（简称G-B量化模式）
  - pertensor-perchannel-pergroup量化模式（简称T-CG量化模式）
  - perblock-perblock量化模式（简称B-B量化模式）
- 伪量化：一般是指对权重矩阵（weight）进行量化的模式，包括perchannel量化模式（简称C量化模式）。
- MX量化（Microscaling Formats）：指由开放计算项目（OCP）制定的低精度数据表示方式。属于pergroup量化模式，表示量化参数类型为FLOAT8_E8M0且gourp size为32的特例情况。

---

<!-- BEGIN op-scene rules（来源: D:\download\op_test_scene.md.txt | 复制日期 2026-08-15 | 后续规则变更只替换本标记间内容） -->

# 算子文档场景提取

## 适用场景

用户提供一个**单个算子 API 文档**（aclnn 系列算子文档，.md 文件路径或直接粘贴内容），需要从文档**全文**中按**设备类型 → 量化模板 → 特性参数**三级分层提取内容，整理成结构化输出。

## 输入

- 文档文件路径（如 `ops-nn/aclnnMatmul.md`），或直接粘贴的文档内容
- 若用户给出目录/多个文档：逐个文档独立分析，每个文档单独成节

## 分析步骤

1. **通读全文**：阅读文档和约束相关的小节——产品支持情况、函数原型、参数说明、返回值错误场景表、约束说明、调用示例等，找出所有和**量化**相关的内容（比如量化、非量化、伪量化、全量化、反量化、后量化/PostQuant）。
2. **识别后量化支持范围**：从文档的"后量化参数组（PostQuantChecker）"等约束章节中，识别哪些量化场景支持后量化、哪些不支持。常见规则：非量化和部分伪量化场景支持后量化（如伪量化 mode=0/1），全量化场景通常不支持后量化（文档会明确标注"不支持后量化"）。
3. **识别算子特性参数**：在参数说明和约束说明中识别和上一步识别到的量化模板相关的参数，比如xxx参数支持xxx量化场景等关键信息。对于伪量化等包含多子模式（mode）的模板，mode 的取值范围作为特性参数列出，不将每个 mode 拆分为独立模板。
4. **过滤掉干扰信息**：并不是文档中只要提到了**场景就要提取出该场景，必须要不仅提到了，而且后文还针对这个算子提出了该场景下各个参数的取值约束，那么才认为这个算子是有这个场景的测试需求
5. **不提取错误/校验场景**：返回值错误场景表中的 ACLNN_ERR_*（161001/161002/561002/561103/361001 等）报错内容、参数校验失败的报错说明，一律不提取。
6. **识别设备类型标注**：按下表将场景划分到设备组；没有设备标注的内容归入"通用"。
7. **量化模式推导**：按"量化模式推导规则"（见下文）判断每个量化模板对应的左右矩阵量化模式，得出 T-C/T-T/K-C/K-T/G-B 等命名。
8. **整理输出**：按"设备类型 → 量化模板 → 特性参数"严格的三级组织，所有量化模板在二级层级平铺排列，格式见"输出格式"。

## 设备类型划分

| 文档原文标注 | 输出组名 | 说明 |
|---|---|---|
| （无任何设备标注） | 不单独成组 | 适用所有设备，模板与特性参数合并到每个具体设备组下；某设备组与无标注内容完全一致时只注明"与无标注内容相同" |
| Atlas 350 加速卡 | **Atlas 350 加速卡** | 简写 350 |
| Atlas A2 训练系列产品 / Atlas A2 推理系列产品 | **Atlas A2 训练/推理系列** | 成对出现时合并为一组，简写 A2 |
| Atlas A3 训练系列产品 / Atlas A3 推理系列产品 | **Atlas A3 训练/推理系列** | 同上，简写 A3 |
| Atlas 推理系列产品（未标注 A2/A3） | **Atlas 推理系列产品** | 推理卡场景归此组 |
| Atlas 训练系列产品（未标注 A2/A3） | **Atlas 训练系列产品** | | 
| Atlas 200I/500 A2 推理产品 | **Atlas 200I/500 A2 推理产品** | |
| 标准列表之外的设备名（如 "Atlas 推理系列加速卡产品"、"Ascend950 系列平台"） | 按原文归组 | 组名注明"（原文设备名）" |

规则：
- **不设"通用"组**：无设备标注的内容适用所有设备，其量化模板（模板定义+特性参数）完整合并到每个具体设备组下，与设备特有内容共同列出；设备组内容与无标注内容完全一致时只注明"与无标注内容相同"
- 一条场景同时适用于多个设备（如 "Atlas 350 加速卡、Atlas 200I/500 A2 推理产品不支持XX"）→ 在每个相关设备组下都列出
- 文档中设备标注与 A2/A3 成对出现时（如 "Atlas A2 训练系列产品/Atlas A2 推理系列产品、Atlas A3 训练系列产品/Atlas A3 推理系列产品"）→ 分别归入 A2 组与 A3 组
- 某个设备组无内容时省略不写

## 量化模式推导规则

量化模板的命名（如 T-C、T-T、K-C、K-T、G-B 等）由**左矩阵（x）和右矩阵（weight）各自的量化模式**组合推导得出，推导遵循以下三步：

### 第一步：确定控制左右矩阵量化的参数

不同算子的参数命名和 shape 符号可能不同（如有的叫 `scaleOptional`，有的叫 `scale`，shape 中的维度名也不一定是 g/N/M）。**必须从文档的实际约束表中读取**，不可自行推断。具体方法如下：

1. **找到控制右矩阵（weight）量化的参数**：在文档约束说明中查找每个量化场景下的 **scale 类参数的 shape 表**（表名通常为"xxx shape"或类似名称）。该表中会列出不同子场景（如 perchannel、pertensor、pergroup 等）对应的 shape。
2. **找到控制左矩阵（x）量化的参数**：在文档中查找**与 x 的量化粒度相关的参数**（常见如 perTokenScale、pertokenScale 等），找到其 shape 表。
3. **伪量化场景**：控制右矩阵量化的参数通常是 **antiquantScale** 类参数，通过其**维度数**或 **shape 是否包含 group 相关维度**来区分 perchannel 与 pergroup。

> **关键原则**：参数名、shape 符号（如 g, N, M, E 等）因算子而异。以上提到的参数名和符号仅为 aclnnGroupedMatmulV5 的举例，实际执行时必须**从当前算子文档中读取对应的参数名和 shape 约定**。

### 第二步：分别判断左矩阵和右矩阵的量化模式

对每个量化场景（如"动态量化"、"静态量化"、"伪量化"等），依次执行：

1. **判断右矩阵（weight）量化模式**：
   - 找到该场景下**右矩阵 scale 参数的 shape 表**（第一步中确定的参数）
   - 根据 shape 表的"子场景"列与对应的 shape，建立映射：
     - 文档明确标注 **perchannel** 的子场景 → **C**
     - 文档明确标注 **pertensor** 的子场景 → **T**
     - 文档明确标注 **pergroup** 的子场景 → **G**
     - 文档明确标注 **perblock** 的子场景 → **B**
   - 如果文档中只有唯一一种子场景（如只写了 perchannel），则该场景**只有一个右矩阵量化模式**

2. **判断左矩阵（x）量化模式**：
   - 找到该场景下**左矩阵 scale 参数的 shape 表**（第一步中确定的参数）
   - 同样根据 shape 表的"子场景"列建立映射：
     - 文档标注 **pertoken** → **K**
     - 文档标注 **pertensor** → **T**
     - 文档标注 **pergroup** → **G**
   - **如果左矩阵 scale 参数在该场景中必须为空**（文档注明"以下入参为空"包含该参数），则左矩阵的量化模式**无法区分**，此时不拆分为 T-xx 和 K-xx 等子模板，而是合并为该场景的一个整体模板
   - 对于**MX 量化**（文档中说明为 pergroup-pergroup 特例，量化参数类型为 FLOAT8_E8M0 且 group size 固定为 32），左右矩阵均为 pergroup，统一命名为 **MX**
   - 对于**G-B 量化**（文档中明确定义左矩阵为 pergroup、右矩阵为 perblock），直接命名为 **G-B**

3. **如果某种量化场景下，文档没有提供 shape 区分规则**（例如该场景下右矩阵只支持 perchannel），则**不拆分**为该场景的多个子模式。

### 第三步：组合左右矩阵量化模式得出模板名

将左矩阵和右矩阵的量化模式组合，形成最终模板命名：

| 左矩阵模式 | 右矩阵模式 | 模板命名 |
|---|---|---|
| pertensor (T) | perchannel (C) | **T-C** |
| pertensor (T) | pertensor (T) | **T-T** |
| pertoken (K) | perchannel (C) | **K-C** |
| pertoken (K) | pertensor (T) | **K-T** |
| pergroup (G) | perblock (B) | **G-B** |
| pergroup (G) | pergroup（G），FLOAT8_E8M0，gs=32 | **MX** |
| 无法区分（左矩阵 scale 参数为空） | 任意 | **不拆分**，直接使用场景名（如"静态量化"）|
| 仅右矩阵量化（伪量化） | perchannel (C) | **伪量化-C** |
| 仅右矩阵量化（伪量化） | pergroup (G) | **伪量化-G** |

## 分级提取

严格按照**三级结构**组织，禁止在二级和三级之间插入额外的分类层（如"一、非量化"、"二、伪量化"等）：

1. **设备类型**（第一级）：先按设备类型拆分，只提取该算子支持的设备类型，设备类型可以从文档的**产品支持情况**获取到；**无设备标注的内容（通用）不单独成组**，直接合并到每个具体设备组下（该组列出通用模板与设备特有模板的全部内容）
2. **量化模板**（第二级）：量化模板需要在同一层级平铺排列，**禁止再按"非量化/伪量化/全量化"分为大组**。具体拆分规则：
   - 先从文档中分析该算子有哪些量化场景，比如非量化、伪量化、全量化、反量化等等，也有可能具体到数据类型，比如全量化-A8W8、全量化-A4W4
   - **后量化（PostQuant）处理**：后量化不是独立模板，也不作为特性参数挂到基础模板下。而是以"**XX+后量化**"形式与基础量化模板平级排列。例如"伪量化"和"伪量化+后量化"为同级两个独立模板；"非量化"和"非量化+后量化"为同级。+后量化模板的模板定义中需说明必须传入 quantScale2、attentionOut 的限制等。从文档的 PostQuantChecker 章节判断哪些基础模板支持+后量化，不支持的（如全量化明确标注"不支持后量化"）不拆分出+后量化版本
   - **伪量化子模式不拆分模板**：伪量化包含的子模式（如 mode=0,1,2,3,4,5,6 等 per-channel/per-token/pergroup 等）**不作为独立模板拆分**，统一为一个"伪量化"模板和一个"伪量化+后量化"模板。mode 的取值范围及其对应的约束作为特性参数（如 keyAntiquantMode/valueAntiquantMode）列出
   - **分类词不作为模板**：文档中的概括性分类术语（如"该算子支持K-C量化、K-C动态量化和mx量化模式"中的"量化模式"）只是量化场景的上级分类名，不代表具体场景，不提取为与量化模板平级的条目；分类词下的内容（通用说明、参数约束）按归属拆分并入分类下的各真实量化模板
   - **量化模板命名需按"量化模式推导规则"推导得出**（如 T-C、K-T 等），推导结果作为模板名写在模板定义前
3. **特性参数**（第三级）：特性参数比较复杂，可能算子的特性是由一个参数控制，也可能是一组参数；需要从文档中提取全部相关参数，把参数可取的值整理提取出来；有些选项会与量化模板有关联（如 xxx 参数在 xxx 量化场景下必须等于某个值），在划分模板时需要将不兼容的选项排除。对于伪量化等多模式模板，mode 取值相关的约束（如 "Q_S>1 时仅取 0/1"）写在对应特性参数条目的"约束"字段中

补充规则：
- **相关联的特性参数提取**：文档中在某个量化模板下，某个特性参数的取值会影响到其他参数时，一并提取，但关联参数同样只提取有选择型的（本身有枚举/分档可选项）；固定后果不作为关联参数提取
- **参数信息描述**：提取出来的参数最好要加上一段文档中对这参数的描述，直接用文档中的描述就行
- **公共约束不单独成级**：文档中"公共约束/公共说明"等设备组级别的通用约束说明，不作为二级条目与量化模板平级输出。内容按归属拆分：
  - 与某特性参数取值相关的约束（如 x2Offset 的 shape、dtype 要求）→ 提取到该特性参数条目下，作为该参数的"约束"信息（仍按枚举/分档类可选项规则筛选，固定取值不提取）
  - 固定条件（固定数据类型/shape、必传、不支持项、对齐要求等）→ 并入该设备组下各量化模板的模板定义行（该组所有模板均适用）
  - 设备组通用固定说明（兼容性说明、版本演进说明等）→ 并入该设备组下量化模板的模板定义行行

（**错误/校验场景不提取**——返回码报错、参数校验失败的描述一律跳过。）



## 输出格式

```
## <算子名>

**<设备类型组>**:
- **<量化模板>**: <模板定义（固定条件，含公共约束中的固定条件与无设备标注内容，文档原文）；本模板不支持：<特性名>…>
  - **<特性名>**:
    - <参数名>: 取值：<可选项（枚举或范围）>；描述：<文档参数描述>；约束：<公共约束中该参数的取值约束>；关联：<该取值牵动的其他参数约束>
- **<量化模板+后量化>**: <模板定义（同基础模板+后量化约束：必须传入 quantScale2 等）；本模板不支持：…>
  - **<特性名>**:
    - ...
  - **PostQuant（后量化参数）**:
    - quantScale2 模式: 取值：per-tensor/per-channel；描述：输出的量化因子粒度
    - attenOut dtype: 取值：INT8/FP8/...；描述：后量化输出数据类型
```

规则：
- **严格三级结构**：设备类型（一级）→ 量化模板（二级）→ 特性参数（三级），所有量化模板在二级层级平铺排列，禁止插入"一/二/三"等中间分组层级
- **模板定义**：该量化模板的固定条件（数据类型、quantMode 固定值、固定 shape/layout 等，含公共约束中的固定条件与无设备标注内容），直接引用文档原文；模板行末"本模板不支持：…"列出该模板下排除的特性
- **后量化模板**：以"XX+后量化"命名，与基础模板同级平铺。模板定义中说明后量化的固定约束（quantScale2 必须存在、dtype 要求、attenOut 数据类型限制等）。如果基础模板不支持后量化（如全量化明确标注"不支持后量化"），则不拆分出+后量化版本
- **伪量化不拆分 mode**：mode 取值（0-6）作为"keyAntiquantMode/valueAntiquantMode"特性参数列出，不将 mode=0,1,2... 拆分为多个独立模板
- **参数条目格式**：`<参数名>: 取值：<可选项>；描述：<文档参数描述>；约束：…；关联：…`——**取值在前**，描述引用文档原文中对该参数的说明
- **特性参数只提取枚举/分档类可选项**：有选择性的参数指类似枚举的选项（如 sparseMode 取值 0/1/2/3/4），或分多档范围需用户选择其一的（如取值支持 16~1024 与 2048~4096 两档，用户选其中一档）；**单个取值范围不算选择**（用户不可能只取范围内某个值，如 blockSize 16~1024），不作为特性参数提取；只能取唯一值的条件（"仅支持 X"/"必须为 X"/"必传"）同样不提取，归入模板定义
- **通用特性合并到每个量化模板下**：多个模板共用的特性（布局、PA、mask 等）在每个模板下重复列出，不设与模板平级的"通用特性"层
- **关联参数同样只提取有选择型的**：特性参数的取值会牵动其他参数时，作为该条目的"关联"信息提取，但关联参数本身也须有可选项（枚举/分档），并注明与主参数的联动；固定后果（必传、固定 shape/dtype、固定行为）不作为关联参数提取
- **公共约束不单独成级**：设备组级别的"公共约束/公共说明"不输出为与量化模板平级的条目；其中与特性参数取值相关的约束合并到对应特性参数条目（作为该条目的"约束"信息，仍按枚举/分档类可选项规则筛选），固定条件与设备组通用说明并入该设备组各量化模板的模板定义行
- **无可选特性的模板**：只输出模板定义行，不挂特性

## 提取要求

- **错误/校验场景不提取**：ACLNN_ERR_* 返回码、参数校验失败的报错描述一律跳过
- **提取特性参数的时候需要提取到这个特性参数里全部有选择型的条件，如果这个特性参数会牵涉到其他参数，只提取其中有选择型的关联参数（同样须为枚举/分档可选项）**
- 只提炼文档原文中出现的内容：不增补、不推断、不改写语义；数值、类型、范围等关键信息必须保留
- 所有小节都覆盖：产品支持情况表的设备差异、参数说明表中的 "使用说明" 列、约束说明等都要纳入
- 同一算子的同一条场景可能同时属于多个设备组（如 A2+A3 合并标注），需在各组分别列出

## 示例

输入：`aclnnFusedInferAttentionScoreV5.md`（节选）
输出：

```
## aclnnFusedInferAttentionScoreV5

**Atlas A2 训练/推理系列**:
- **非量化**: 模板定义：query/key/value 数据类型仅支持均为FLOAT16/BFLOAT16、数据格式仅支持ND…（无设备标注内容合并到本组，下同）
  - **布局（inputLayout）**:
    - query/key/value 数据类型: 取值：FLOAT16/BFLOAT16；描述：公式中的输入Q/K/V
    - inputLayout: 取值：BNSD/BSND/BSH/TND/NTD/BSH_BNSD/BSND_BNSD/NTD_TND/BNSD_BSND；描述：标识输入query、key、value的数据排布格式
  - **Mask**:
    - sparseMode: 取值：0/1/2/3/4；描述：稀疏计算模式
  - **PagedAttention**:
    - kvCacheRef 排布: 取值：BnBsH/BnNBsD/NZ（BnNBsD 性能更优）；描述：PageAttention中KV存储的排布格式
- **伪量化**:
  - **DequantChecker（反量化）**:
    - keyAntiquantMode/valueAntiquantMode: 取值：0-6（per-channel/tensor、per-token、per-tensor+per-head、per-token+per-head、per-token+PA、per-token+per-head+PA、per-token-group）；描述：key/value的反量化的方式；约束：Q_S>1 且 key/value=INT8 时仅取 0/1；关联：keyAntiquantScale/valueAntiquantScale 为选择型关联参数，dtype/shape 按 mode 取值不同有对应约束
    - key/value 数据类型: 取值：INT8/INT4(INT32)/HIFLOAT8/FLOAT8_E4M3FN/FLOAT4_E2M1（按 keyAntiquantMode 取值筛选）；描述：公式中的输入K/V
  - **布局（inputLayout）**:
    - inputLayout: 取值：BSH/BNSD/BSND/BNSD_BSND/TND；描述：标识输入query、key、value的数据排布格式
- **全量化-GQA**: 模板定义：query/key/value 为 FLOAT8_E4M3FN，attentionOut 为 FLOAT16/BFLOAT16，queryQuantMode=3、keyAntiquantMode=3、valueAntiquantMode=2，inputLayout=NTD_TND，D=128，blockSize=128，kv cache 排布仅 BnNBsD（本模板不支持：prefix/pse/alibi/左padding/tensorlist/rope/后量化）
  - **Mask**:
    - sparseMode: 取值：0/3；描述：稀疏计算模式

**Ascend 950PR/Ascend 950DT** (原文设备名):
- **非量化**: 模板定义：…（无设备标注内容合并到本组；本组额外约束见下）
  - **Mask**:
    - sparseMode: 取值：0/1/2/3/4；描述：稀疏计算模式；约束：取 0/1 时 attenMask 输入维度为 2 维不支持（该组合排除）
```

公共约束处理示例（如 aclnnQuantMatmulV5 中 Atlas A2 训练/推理系列 的"公共约束"部分）：
- 公共约束中"x2Offset 的 shape 是 1 维（t，），t = 1 或 n"等与特性参数取值相关的约束 → 提取到 T-C && T-T 模板的"x2Offset 数据类型"条目下，作为该参数的"约束"信息
- 公共约束中"当前版本不支持 yScale、x1Offset，需要传入 nullptr""x1、x2 为 INT8，out 为 INT32 时各 scale 实际不参与计算"等固定条件 → 并入该设备组各量化模板的模板定义行（如模板行末"本模板不支持：yScale，需传入 nullptr"）
- 公共约束中"兼容 aclnnQuantMatmulV3、V4 接口功能"等设备组通用说明 → 并入该设备组量化模板的模板定义行

<!-- END op-scene rules -->
