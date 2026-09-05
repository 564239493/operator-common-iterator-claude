# share_references — 跨 Skill 共享参考目录

> 本目录存放**多个 skill 共同消费**的参考文件（唯一事实源）。任一 skill 的
> `references/` 只放自己独占的文件；需要跨 skill 共享的一律放这里，**禁止复制副本**
> （双源必然漂移）。

## 引用约定

- 引用路径一律写作 `.claude/skills/share_references/<文件名>`（相对项目根）。
- 消费方在 SKILL.md / agent .md 中写明「读 `share_references/<文件名>` 的哪些部分、
  满足什么条件才读」（条件加载，不满足不读）。
- 修改本目录文件前先确认所有下方登记的消费方语义兼容；改完在下方登记表中更新备注。

## 文件登记表

| 文件 | 内容 | 消费方 | 加载方式 |
|:--|:--|:--|:--|
| `extract_device_type.md` | 从文档"产品支持情况"提取支持设备（R1~R9） | `scan-scenes`（M3 第三步，整篇加载）；`extract-constraints`（设备→product_support 交集收窄，仅 R2/R3/R4/R6 语义） | scan-scenes 整篇读；extract-constraints 条件读 |
| `leveled_feature_definition.md` | 分级特征（设备→模板→特性参数）判定规则 | `scan-scenes`（M3 第二步） | scan-scenes 整篇读 |

## 关键衔接契约（extract_device_type.md）

`scan-scenes` 产出的 `scene_scan.json.device_types` 名称**必须与算子文档
"产品支持情况"表原文逐字一致**（由 R3/R4 保证），`extract-constraints` 据此与
√ 行取交集才能命中；名称被改写/缩写会导致交集静默落空（`render_scene_directive.py`
exit 2 与 `validate_artifacts.py scene_scan` 共同守住该契约）。
