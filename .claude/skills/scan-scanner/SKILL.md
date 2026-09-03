---
name: scan-scanner
description: 当需要从算子文档中提取设备类型、场景、特性参数以及其取值范围时使用。适用于CANN算子文档的分级特征扫描提取，不适用于：算子约束规则抽取，算子执行错误分析。
---

## M1 领域知识
> 职责：提供模型易判错的领域边界
- **特性参数 ≠ 普通可选参数**：存在公共参数，即一个参数可能属于多个特性，判定方法遵循M3第五步，并且禁止私自修改参数名称，必须与原文保持一致.
- **分级特征是三层**：设备类型 → 场景 → 特性, 扫描必须自顶向下,禁止跳层.
- **示例值不是支持值**：代码块样例仅为演示，仅正文明确声明支持的取值可提取.
- **约束按设备类型分组**：同一参数在不同设备下取值可能不同，禁止跨设备合并.

## M2 工具定义
> 职责：定义输出schema，以及schema中每个字段的含义，禁止随意变更输出schema；
- **输入**：算子文档
- **输出schema**：

```json
{
  "operator": "string(算子名称)",
  "has_scenarios": "bool(是否需要进行尝尽划分的标志)",
  "device_types": ["string(支持的设备名称列表)"],
  "confidence": "float(提取结果置信度，取值必须在0~1之间，1是绝对可信，0是完全不可信)",
  "devices": [
    {
      "device": "string(单个设备名称)",
      "scenes": [
        {
          "template": "string(场景名称)",
          "unferture_params": "string(非特性参数的描述)",
          "unsupported_params": ["string(当前设备和场景下不支持参数)"],
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
  "scan_notes": [{"kind":"string(quant_signal_no_template或'')","message":"string(判定不涉及场景的依据描述)"}]
}
```
- **输出示例(仅参考，禁止作为事实源进行推导)**
`references/scene_scan_success.json`

## M3 工作流程
> 职责: 定义提取步骤，明确每个步骤用到的提取规则.md；
> 纪律：禁止跳步，提取规则文件缺失时，报错并终止流程，讲报错信息在调度消息中反馈给用户决策，禁止凭记忆继续提取

- **第一步：加载规则文件(必须执行)**
1. `references/leveled_feature_definition.md`
2. `references/extract_device_type.md`
3. `references/extract_scanes.md`
4. `references/extract_feature_params.md`

- **第二步：分级特征扫描**
1. 基于`leveled_feature_definition.md`中定义的每个层级的判定规则，确定算子是否需要进行设备、场景、特性参数三级提取的场景;
2. 如果没有命中任何一个层级的提取特征，则在结果json中`has_scenarios`字段记录结果，输出结果json文件，并终止场景扫描流程，将判定结果通过调度消息反馈给用户
3. 如果命中提取特征，则执行下一步。

- **第三步：提取设备类型**
1. 基于`extract_device_type.md`中定义的`提取规则`，提取算子支持设备信息；
2. 支持设备信息写入结果json的`device_types`字段。

- **第四步：提取场景**
1. 针对`device_types`中的每一个device，基于`extract_scanes.md`中定义的`提取规则`，提取该设备下的支持的场景信息；
2. 基于输出schema中`devices`字段下每个元素的定义，填充对应的字段值。
3. 如果从算子文档中捕获到了场景划分的信息，但是没有与`extract_scanes.md`中定义的`场景模板`中的任何一个模板匹配，则在`scan_notes`中记录，此时`kind`字段值填写'quant_signal_no_template'，并在`message`字段中记录判定依据。

- **第五步：提取特性参数**
1. 获取算子所有的合法参数；
2. 遍历算子的每一个参数，基于`extract_feature_params.md`中定义的`特性参数判定规则`，判定该算子参数是否为特性参数，如果是，则根据schema中的`feature_params`字段模板构建输出结果，如果不是特性参数，则在`unsupported_feature_params`记录判定依据；
3. 如果算子参数是特性参数，则根据`extract_feature_params.md`中定义的`特性参数取值范围提取规则`提取参数的可取值范围，并在`feature_params`字段记录结果；
4. 根据`extract_feature_params.md`中定义的`参数约束特征提取规则`提取该场景下算子参数的约束描述，并在`feature_params`字段记录结果，约束描述必须与原文保持一致。

-- **第六步：提取结果文件生成**
将完整的提取结果写入json文件。

-- **第七步：验证**
1. 使用脚本校验输出产物，执行命令：`python scripts/validate_artifacts.py scene_scan <run-dir>/inputs/scene_scan.json`；
2. 校验未通过，则根据报错信息修正输出结果，即重新执行第三步、第四步、第五步中的其中一步或所有步骤。
3. 根据验证结果填写输出schema中的`confidence`值。

# M4 安全红线
> 职责：绝对禁止项

- 禁止提取文档未声明的内容，禁止使用训练常识不全文档缺失的参数取值；
- 禁止跳过第一步，规则文件缺失则必须终止，禁止静默跳过；
- 算子文档和规则文件冲突时，以规则文件为准；遇到规则未覆盖的新情况时，`confidence`的值不得超过0.2，禁止现场发明规则
- 禁止在输出中伪造`confidence`，必须客观公正的评价输出质量，给出合理的置信度。
- 禁止在结果中设置"common"组，每个设备的数据独立记录，即使两个设备的场景+特性参数数据完全一致，也禁止合并为通用组
- 禁止推断和修改设备名称以及参数名称，必须严格按照文档中的原始内容填写。
