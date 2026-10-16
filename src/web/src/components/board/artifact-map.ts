/** 各 agent 关注的产物路径表（相对 run 根；inputs/ 与 iter_NNN/ 两级） */

export interface ArtifactEntry {
  label: string
  path: (n: number) => string
}

export function pad3(n: number): string {
  return String(n).padStart(3, '0')
}

export const ARTIFACT_MAP: Record<string, ArtifactEntry[]> = {
  'scene-scanner': [
    { label: 'scene_scan.json', path: () => 'inputs/scene_scan.json' },
    { label: 'selection.json', path: () => 'inputs/selection.json' },
    { label: 'scene_conflicts.json', path: () => 'inputs/scene_conflicts.json' },
  ],
  'constraint-extractor': [
    { label: 'constraints.json', path: (n) => `iter_${pad3(n)}/constraints.json` },
    { label: 'extraction_provenance.json', path: (n) => `iter_${pad3(n)}/extraction_provenance.json` },
  ],
  'source-analyst': [{ label: 'source_evidence.json', path: (n) => `iter_${pad3(n)}/source_evidence.json` }],
  'constraint-supplementer': [
    { label: 'constraints.json（合并后）', path: (n) => `iter_${pad3(n)}/constraints.json` },
    { label: 'conflict_resolution.json（用户裁决）', path: () => 'inputs/conflict_resolution.json' },
  ],
  'constraint-checker': [
    { label: 'constraint_check.json', path: (n) => `iter_${pad3(n)}/constraint_check.json` },
    { label: 'relation_examples.json', path: (n) => `iter_${pad3(n)}/relation_examples.json` },
  ],
  'constraint-repairer': [
    { label: 'constraints.json（修复后）', path: (n) => `iter_${pad3(n)}/constraints.json` },
  ],
  'case-generator': [
    { label: 'generation_summary.json', path: (n) => `iter_${pad3(n)}/generation_summary.json` },
    { label: 'generation_progress.json', path: (n) => `iter_${pad3(n)}/generation_progress.json` },
    { label: 'cases.json', path: (n) => `iter_${pad3(n)}/cases.json` },
  ],
  'case-executor': [
    { label: 'execution_result.json', path: (n) => `iter_${pad3(n)}/execution_result.json` },
    { label: 'cases_expanded.json', path: (n) => `iter_${pad3(n)}/cases_expanded.json` },
    // TTK 框架特有产物（ATK 轮次不出现，属正常缺失）
    { label: 'ttk_conversion_audit.json（TTK）', path: (n) => `iter_${pad3(n)}/ttk_conversion_audit.json` },
    { label: 'golden_manifest.json（TTK）', path: (n) => `iter_${pad3(n)}/golden_manifest.json` },
  ],
  'quality-reviewer': [{ label: 'quality_gate.json', path: (n) => `iter_${pad3(n)}/quality_gate.json` }],
  'failure-analyst': [{ label: 'analysis.json', path: (n) => `iter_${pad3(n)}/analysis.json` }],
  'constraint-updater': [
    { label: 'constraint_update.json', path: (n) => `iter_${pad3(n)}/constraint_update.json` },
    { label: 'regression_check.json', path: (n) => `iter_${pad3(n)}/regression_check.json` },
    { label: 'constraints_diff.json', path: (n) => `iter_${pad3(n)}/constraints_diff.json` },
  ],
  'prompt-optimizer': [
    { label: 'prompt_update_decisions.json', path: () => 'inputs/prompt_update_decisions.json' },
  ],
}
