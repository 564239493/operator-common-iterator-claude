// 与后端视图模型对应的 TS 类型（宽松声明，字段以后端为准）

export type AgentStatus =
  | 'pending' | 'running' | 'passed' | 'rejected' | 'skipped' | 'unconfirmed' | 'not_involved'

export interface AgentRuntime {
  name: string
  role: string
  stage: string | null
  optional: boolean
  status: AgentStatus
  basis: string
  inferred: boolean
  iteration: number | null
  ruleset?: string
  // supplementer / optimizer 的补充展示字段（后端随状态条目附带）
  application?: string | null
  origins?: Record<string, number>
  decisions?: { status?: string; count?: number; error?: string }
}

export interface AgentDef {
  name: string
  description: string
  skills: string[]
  tools: string
  color: string
  mode?: string
  permission?: Record<string, unknown> | null
  definition_found?: boolean
  load_error?: string | null
  source?: string | null
  role: string
  optional: boolean
  stage: string | null
  file: string | null
}

export interface RunSummary {
  run_id: string
  operator?: string
  state?: string
  is_terminal?: boolean
  current_iteration?: number
  max_iterations?: number
  test_framework?: string
  mode?: string
  created_at?: string
  updated_at?: string
  last_activity?: string
  iteration_dirs?: number
  parse_error?: string
}

export interface ReplayEvent {
  iteration: number | null
  agent: string
  action: 'passed' | 'rejected' | 'running' | 'unconfirmed' | 'skipped' | 'handoff' | 'terminal'
  to_agent?: string
  to_round?: number
  basis: string
  at?: string | null
  inferred: boolean
  state?: string
  application?: string | null
  origins?: Record<string, number>
  decisions?: { status?: string; count?: number; error?: string }
}

export const STATUS_TEXT: Record<string, string> = {
  pending: '待运行',
  running: '运行中',
  passed: '已通过',
  rejected: '打回',
  skipped: '跳过',
  unconfirmed: '待确认',
  not_involved: '未参与',
}

export const STATUS_COLOR: Record<string, string> = {
  pending: '#909399',
  running: '#e6a23c',
  passed: '#67c23a',
  rejected: '#f56c6c',
  skipped: '#6b7280',
  unconfirmed: '#b0a06a', // 灰黄：证据不足 ≠ 跳过 ≠ 完成
  not_involved: '#4b5563',
}
