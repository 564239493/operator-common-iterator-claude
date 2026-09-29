// 与后端视图模型对应的 TS 类型（宽松声明，字段以后端为准）

export type AgentStatus = 'pending' | 'running' | 'passed' | 'rejected' | 'skipped' | 'not_involved'

export interface AgentRuntime {
  name: string
  role: string
  stage: string | null
  optional: boolean
  status: AgentStatus
  basis: string
  inferred: boolean
  iteration: number | null
}

export interface AgentDef {
  name: string
  description: string
  skills: string[]
  tools: string
  color: string
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
  action: 'passed' | 'rejected' | 'handoff' | 'skipped' | 'terminal'
  to_agent?: string
  basis: string
  at?: string | null
  inferred: boolean
  state?: string
}

export const STATUS_TEXT: Record<string, string> = {
  pending: '待运行',
  running: '运行中',
  passed: '已通过',
  rejected: '打回',
  skipped: '跳过',
  not_involved: '未参与',
}

export const STATUS_COLOR: Record<string, string> = {
  pending: '#909399',
  running: '#e6a23c',
  passed: '#67c23a',
  rejected: '#f56c6c',
  skipped: '#6b7280',
  not_involved: '#4b5563',
}
