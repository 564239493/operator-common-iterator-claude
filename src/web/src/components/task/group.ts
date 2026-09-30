// 任务选择的纯函数核心：按「算子名 × 一次测试运行（创建时间）」组织 /api/runs 列表。
// 无 Vue 依赖，vitest 直接覆盖（模式同 board/graph.ts）。
import type { RunSummary } from '../../api/types'

export interface OperatorGroup {
  operator: string
  runs: RunSummary[]
  latest: RunSummary
}

/** 算子名兜底：operator 缺失时按目录名 <算子名>-<日期>-<时间>-<微秒> 从右去掉后三段 */
export function operatorOf(r: RunSummary): string {
  if (r.operator) return r.operator
  const parts = r.run_id.split('-')
  return parts.length > 3 ? parts.slice(0, -3).join('-') : r.run_id
}

/** 解析时间：无时区标记的 ISO 按 UTC 补 Z（created_at 是 UTC，裸解析会偏移到本地） */
export function parseTaskTime(iso?: string | null): Date | null {
  if (!iso) return null
  const s = /[Zz]$|[+\-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

function timeValue(iso?: string | null): number {
  const d = parseTaskTime(iso)
  return d ? d.getTime() : Number.NEGATIVE_INFINITY
}

function byTimeDesc(a?: string | null, b?: string | null): number {
  const ta = timeValue(a)
  const tb = timeValue(b)
  if (ta === tb) return 0
  return tb - ta
}

/**
 * 按算子分组：parse_error 的运行不进选择器（历史抽屉仍可见）；
 * 组间按组内最新运行倒序，组内按创建时间倒序，缺失/非法时间排最后。
 */
export function groupRunsByOperator(runs: RunSummary[]): OperatorGroup[] {
  const byOp = new Map<string, RunSummary[]>()
  for (const r of runs) {
    if (r.parse_error) continue
    const op = operatorOf(r)
    const list = byOp.get(op)
    if (list) list.push(r)
    else byOp.set(op, [r])
  }
  const groups: OperatorGroup[] = []
  for (const [operator, list] of byOp) {
    list.sort((a, b) => byTimeDesc(a.created_at, b.created_at))
    groups.push({ operator, runs: list, latest: list[0] })
  }
  groups.sort((a, b) => byTimeDesc(a.latest.created_at, b.latest.created_at))
  return groups
}

/** 默认选中的任务：最新的非 parse_error 运行 */
export function pickDefaultRun(runs: RunSummary[]): RunSummary | null {
  let best: RunSummary | null = null
  for (const r of runs) {
    if (r.parse_error) continue
    if (!best || byTimeDesc(best.created_at, r.created_at) > 0) best = r
  }
  return best
}

/** 选项时间（本地时区 MM-DD HH:mm）；无法解析时原样展示 */
export function fmtTaskTime(iso?: string | null): string {
  const d = parseTaskTime(iso)
  if (!d) return iso || '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 选项单行标签：时间 · 状态 · 第X/Y轮 */
export function fmtTaskOption(r: RunSummary): string {
  const cur = r.current_iteration ?? '—'
  const max = r.max_iterations ?? '—'
  return `${fmtTaskTime(r.created_at)} · ${r.state || '…'} · 第${cur}/${max}轮`
}

/** 状态标签配色（与历史抽屉 stateType 同判） */
export function stateTagType(state?: string): 'success' | 'danger' | 'warning' | 'primary' | 'info' {
  if (!state) return 'info'
  if (state === 'SUCCESS') return 'success'
  if (state.startsWith('STOP') || state === 'BLOCKED') return 'danger'
  if (state === 'MAX_ITERATIONS') return 'warning'
  return 'primary'
}
