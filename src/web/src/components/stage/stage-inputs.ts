/** Role contracts, not an audit of actual file reads. Presence is reported separately. */
export function stageInputs(role: string, round: number, run: any) {
  const iter = (n: number) => (run?.iterations || []).find((i: any) => i.n === n)
  const file = (name: string, n = round, label = name) => ({ label, path: `iter_${String(n).padStart(3,'0')}/${name}`, evidence: iter(n)?.exists?.[name] === true ? '已确认' : iter(n)?.exists?.[name] === false ? '未找到' : '' })
  const doc = {label:'算子文档快照',path:run?.operator_doc || '路径未提供',evidence:'任务配置'}
  const prompt = {label:'提示词快照',path:run?.current_prompt || '路径未提供',evidence:'任务配置'}
  const state = {label:'任务配置',path:'run_state.json',evidence:'当前任务记录'}
  const constraints = () => file('constraints.json',round,'当前约束')
  const check = () => file('constraint_check.json',round,'约束检查报告')
  const cases = () => file('cases.json',round,'生成的用例')
  const execution = () => file('execution_result.json',round,'执行结果')
  switch(role) {
    case 'scene-scanner': return [doc]
    case 'constraint-extractor': return [doc,prompt,{label:'场景指令（如已生成）',path:'inputs/scene_directive.md',evidence:'按流程定义引用，存在性未核实'}]
    case 'constraint-checker': return [doc,constraints(),...(round===1?[file('extraction_provenance.json',1,'知识应用报告')]:[file('constraint_update.json',round,'更新报告（自动更新路径）')])]
    case 'constraint-repairer': return [doc,constraints(),check()]
    case 'case-generator': return [constraints(),state]
    case 'case-executor': return [cases(),...(run?.test_framework==='ttk'?[file('cases_ttk.csv',round,'TTK 用例')]:[doc]),state]
    case 'quality-reviewer': return [constraints(),check(),cases(),execution()]
    case 'failure-analyst': return [doc,constraints(),cases(),execution()]
    case 'constraint-updater': return round>1?[doc,file('constraints.json',round-1,'上一轮约束'),file('analysis.json',round-1,'上一轮诊断'),file('execution_result.json',round-1,'上一轮执行结果')]:[]
    case 'constraint-supplementer': return [constraints(),{label:'补充证据（可选）',path:'inputs/supplementary-doc.md / inputs/supplement_constraints.md',evidence:'需由具体任务提供'}]
    case 'source-analyst': return [doc,{label:'源码快照（可选）',path:'inputs/src_snapshot/',evidence:'存在性未核实；提取与诊断路径所需材料不同'}]
    case 'prompt-optimizer': return [doc,prompt,file('analysis.json',round,'诊断证据（如已生成）')]
    default:return []
  }
}
