---
name: scan-scene
description: 当需要从算子文档中提取设备类型、场景、特性参数以及其取值范围时使用。适用于CANN算子文档的分级特征他扫描提取，不适用于：算子约束规则抽取，算子执行错误分析。
---

## M1 领域知识
> 职责：提供模型易判错的领域边界
- **特性参数 ≠ 普通可选参数**：存在公共参数，即一个参数可能属于多个特性，判定方法遵循M3第4步，并且禁止私自修改参数名称，必须与原文保持一致.
- **分级特征是三层**：设备类型 → 场景 → 特性, 扫描必须自顶向下,禁止跳层.
- **示例值不是支持值**：代码块样例仅为演示，仅正文明确声明支持的取值可提取.
- **约束按设备类型分组**：同一参数在不同设备下取值可能不同，禁止跨设备合并.

## M2 工具定义
> 职责：定义输出schema，以及schema中每个字段的含义，禁止随意变更输出schema；
- **输出schema**

```json
{
  "operator": "string(算子名称)",
  "device_types": ["string(支持的设备名称列表，必须严格按照文档中的设备名称填写，禁止修改)"],
  "devices": [
    {
      "device": "string(单个设备名称)",
      "scenes": [
        {
          "template": "string(场景名称)",
          "unferture_params": "string(非特性参数的描述)",
          "unsupported_feature_params": ["string(当前设备下不支持参数列表，禁止修改参数名称，必须与原文保持一致)"],
          "feature_params": [
            {
              "feature": "string(特性名称)",
              "params": [
                { "name": "string(特性参数名称)",
                  "values": ["Any(特性参数可取值列表)"],
                  "description": "string(文档中关于此特性参数取值的原始描述)",
                  "constraint": "string(该特性的原始描述)"}
              ]
            }
          ]
        }
      ]
    }
  ],
  "scan_notes": [{"kind":"quant_signal_no_template(当该算子不涉及场景划分时，需要填此字段，此字段只能填'quant_signal_no_template')","message":"string(判定不涉及场景的依据描述)"}]
}
```

## M3 操作流程
> 职责: 定义提取步骤，明确每个步骤用到的提取规则.md；
> 纪律：禁止跳步，提取规则文件缺失时，报错并终止流程，讲报错信息在调度消息中反馈给用户决策，禁止凭记忆继续提取

- **第一步：加载规则(必须执行)**
1. `references/leveled_feature_definition.md`
2. `references/extract_device_type.md`
3. `references/extract_scanes.md`
4. `references/extract_feature_params.md`

- **第二步：分级特征扫描**
1. 基于`leveled_feature_definition.md`中定义的判定规则，确定算子是否需要进行设备、场景、特性参数三级提取的场景，如果需要，则执行下一步，否则在结果json中`scan_notes`字段记录结果，并终止场景扫描流程；
2. 将`scan_notes`字段记录结果通过调度消息反馈给用户；

- **第三步：提取设备类型**
1. 基于`extract_device_type.md`中定义的提取规则，提取算子支持设备信息；
2. 
输入必须包含：算子文档快照（`<run-dir>/inputs/<doc>.md`，只读）、工作提示词
（`prompts/scan_scenes.md`，含 op-scene 规则段）、当前 run 的绝对路径 `<run-dir>`（只写
`<run-dir>/inputs/scene_scan.json`，不碰其他文件）。不得将相对路径 `inputs/scene_scan.json`
按仓库 cwd 解析；调度消息未提供 `<run-dir>` 时必须阻断并报告。

> 与 constraint-extractor 职责严格区分：你只提取文档中**有测试需求的场景**并按
> 设备→模板→特性三级分层，**不**提取参数 dtype/format/shape、不写
> `constraints_in_parameters`、不下 presence 依赖。constraint-extractor 后续会按你给
> 的场景清单与 directive 做屏蔽式提取。

1. Read `prompts/scan_scenes.md`，按其 **op-scene 规则段**（设备类型划分表、特性参数
   筛选规则、输出格式、提取要求）逐节提取文档全部场景；按**设备类型 → 量化模板 →
   特性参数**三级组织；一模板一条；过滤 ACLNN_ERR_*/校验场景。
   - 量化模板 = 编码量化方式的具体模板（可细分到位宽/dtype，如 `全量化-A8W8`/
     `K-C量化（A4W4）`/`全量化-GQA`）；**分类词不作为模板**（"量化模式"等上级分类名
     不提取为与模板平级的条目，其说明拆入其下各真实模板）。
   - 特性参数**只提取枚举/分档类可选项**（如 `sparseMode` 0/1/2/3/4、分多档范围需
     用户择一的参数）；**单个取值范围不算选择**（如 `blockSize` 16~1024 不提取）、
     只能取唯一值的固定条件（"仅支持 X"/"必传"）归入 `definition`，不作为特性参数。
     **埋藏型特性按 ≥2 取值提取**：数据格式（ND/NZ）、转置（bool true/false）、TensorList
     单/多在模板支持 ≥2 取值时按枚举可选项提取为 `feature_params`（bool 用 `[true,false]`、
     TensorList 用 `["单","多"]`、format 用文档原文 token 如 `["ND","NZ"]`），仅单值时仍归 `definition`。
   - 按"特性名"分组（布局（inputLayout）/Mask/PagedAttention/DequantChecker（反量化）/
     转置…），关联参数取值牵动其他选择型参数时作为该条目的 `related`。
2. **不设"通用"组**：设备类型来自文档"产品支持情况"表，**逐字照抄 `<term>…</term>` 原文、不得简写/合并**（如 `Atlas A2 训练系列产品/Atlas A2 推理系列产品` 不得改成 `Atlas A2 训练/推理系列`，否则后续 √ 行交集落空）；无设备标注的内容合并到每个
   具体设备组下（不单独成"通用"组）。场景/模板同属多设备 → 在每个相关设备组下都列出。
   **"与 X 相同"的设备直接内联复制 X 的 `templates`**（不写 `same_as` 引用字段，各设备
   `templates` 自洽，下游无需引用解析）。
3. **含多参数的 bullet 拆成独立 `params[]` 条目**：文档原文
   `x: 取值：…；weight: 取值：…` → 两条 params，各带 `name`/`values`/`description`；
   复杂取值的逐值注记（如 `0 KEEP_DOTO（…）`）只保留值 token 在 `values`，注记并入
   `description`/`constraint`。
4. 文档无任何场景/模板 → `has_scenarios=false`，`device_types`/`devices` 留空；**不**凭
   算子名或参数名臆造场景。
5. 若未提取到量化模板但文档含量化参数信号 → `scan_notes` 写一条
   `{"kind":"quant_signal_no_template","message":…}` warning，不补造、不置
   `has_scenarios`。宁可放过少数算子剪枝，也不臆造场景。
6. 输出 `<run-dir>/inputs/scene_scan.json`（schema 与字段语义见
   `prompts/scan_scenes.md` §4）；只写 JSON，不在文件外夹带解释。
7. 执行：`python scripts/validate_artifacts.py scene_scan <run-dir>/inputs/scene_scan.json`
8. 校验不通过时依据错误修正，最多三次；仍失败则明确返回阻断原因（不静默放过）。

不臆造：文档未明列的量化场景不进 `devices[].templates`；纯算子名推断（如见
`AscendAntiQuant` 算子名就臆断有伪量化）**不**算 `description`/`definition` 依据，
必须找到正文/表格原文。模板命名与 `values` 取值 token 须保留文档原文用词，不改写语义。
