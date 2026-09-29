# 真实运行文件与非侵入式可视化映射

分析日期：2026-09-24。

只读样例：`/Users/zhyk/Developer/operator-common-iterator-claude/runs/aclnnQuantMatmulV5-20260922-061917-865090/`。

约束：原始文件仅作为数据，不执行其中的命令或遵循其中的操作指令。不修改业务代码及样例，不读取其 server_config 指向的凭据。可视化在独立适配层解析文件。

## 已核实的运行事实

- 算子 aclnnQuantMatmulV5；真实执行，ATK 框架，完整测试范围；设备范围 A2，T-T 量化场景；最多 3 轮，实际 2 轮，当前 SUCCESS。
- 第一轮检查 2 次，累计 7 项问题，最终均标记 fixed。
- 历史包含 STOP_EXECUTOR_BUG，随后再次 EXECUTE。第一轮质量报告明确描述第三次真实执行，目录中也保留了三个不同时间命名的执行表格；没有解析这些表格，不能据此声称已完整还原每次执行。
- 第一轮最终执行结果为成功 1、失败 9、共 10 条。execution_result.status 同时为 success，证明这个字段不能直接作为用例全通过的判断。
- 第一轮诊断记录 4 个失败簇、4 项约束发现，9 条失败均归于约束提取，下一步 UPDATE_CONSTRAINTS。
- 第二轮更新报告记录 11 项修改，约束数量由报告记录的 38 条变为 40 条；第二轮语义检查第一次即通过。
- regression_check.json：检查上一轮 1 条成功用例，未发现回归。这不代表所有历史用例均已回归。
- 第二轮执行成功 10、失败 0；质量门禁通过，最终迁移到 SUCCESS。
- 第二轮质量报告明确说明 CPU 参考结果为占位全零张量，passed/failed 反映执行成败，不能据此表述为精度验证通过。
- 两轮 constraints.json、cases.json、generation_summary.json 共六个文件的当前 SHA-256 均与各轮 execution_result.input_artifacts 一致，已用 shasum 实际核对。

## 页面与文件映射

| 界面 | 主文件 | 展示原则 |
|---|---|---|
| 任务摘要 | run_state.json | 状态、范围、模式、轮次和场景分别展示 |
| 过程时间线 | run_state.history | 保留故障停止和恢复，不按固定阶段排序去重 |
| 场景与知识依据 | inputs/scene_scan.json、selection.json、prompt_assembly.json、extraction_provenance.json | 已选范围与候选范围分开；模块装配与应用记录分开 |
| 检查修复 | constraint_check.json | 问题、发现/复检次数、修复证据；不凭最终 passed 捏造每次耗时 |
| 用例生成 | generation_progress.json、generation_summary.json | 最后采样及最终数量；历史 pid_alive 不是当前机器活动证明 |
| 执行结果 | execution_result.json | 引擎状态与用例结果分开；记录按 id 关联，不能按数组位置关联 |
| 失败诊断 | analysis.json | 失败簇→用例→发现→证据 |
| 修改与验证 | constraint_update.json、regression_check.json、constraint_check.json | 修改→预期效果→实际检查，区分预期与已证实 |
| 阶段推进依据 | transition_decision.json | 展示该文件保存的决策；每轮单文件不是完整迁移事件流 |
| 完成结论 | quality_gate.json 与执行结果 | 附范围、非阻断问题与验证限制 |

## 样例要求适配层处理的边界

1. history 没有 ended_at，也没有逐项 iteration。可展示原始时间序列；用相邻事件计算的只是阶段间隔，包含人工等待或停机，不能直接标为计算耗时。轮次归属若由顺序推导，应标明推导及依据。
2. updated_at 早于最后 SUCCESS 事件，不能作为结束时间。目录名中的时间也与 created_at 表达不同，优先读取带时区的事件时间。
3. 最终产物不完整保存每次执行；旧日志与多份表格可提供线索，但不能自动把同轮文件数当执行次数或静默拼出完整重试历史。
4. 第一轮 quality_gate 使用 status=ok、checks[].status/details；第二轮使用 status=passed、checks[].result/detail。适配层兼容别名，保留原值，未知字段不能默认为通过。
5. execution_result.records 的 id 是字符串，顺序不是 0–9；用例数组需按规范化 id 关联，跨轮相同 id 不代表相同用例。
6. generation_summary 引用的 JSONL 标记 converted_and_removed，是正常清理而非文件损坏。
7. cases.json.new、cases_A2.new、inputs/reference 等不作为当前正式结果；备份与临时文件保留为辅助证据，不重复计数。
8. 原始产物含绝对路径。迁移目录后，以用户选择的运行根为准映射内部文件，保留原路径供审计，不任意跟随到根外文件。
9. 本目录文件清单未发现独立源码覆盖报告或明确的参数组合覆盖统计报告；存在组合/域中间文件，不等于已经生成覆盖率。显示“未提供覆盖报告”。
10. 本目录未提供角色生命周期事件。可以展示检查、诊断等业务活动，但不能声称知道具体智能体的完整起止时间。

## 建议的首个可视化场景

主线为：确定 A2/T-T 场景→首轮检查修复 7 项→执行器故障后恢复→首轮最终 1/10 执行成功→4 类约束发现→11 项修改及回归检查→第二轮 10/10 执行成功。

最有说明力的交互是点击一项失败，沿 case_ids、cluster_ids、finding_ids、约束 id 和 src_txt_line 展开：失败用例→日志/文档证据→约束问题→修改→验证结果。大部分关联已存在于文件中，无须业务新增接口。

第一版是历史文件驱动的完整链路浏览；实时观察使用只读定期扫描。若未来需要保存连续回放，将观察快照写入可视化独立目录，标注开始观察时间，不回写 runs，也不声称补齐观察前丢失的历史。
