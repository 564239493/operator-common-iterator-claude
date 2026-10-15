import { describe, expect, it } from 'vitest'
import { fmtTaskOption, fmtTaskTime, groupRunsByOperator, operatorOf, pickDefaultRun, stateTagType } from './group'
import type { RunSummary } from '../../api/types'

function run(over: Partial<RunSummary> & { run_id: string }): RunSummary {
  return { ...over }
}

describe('groupRunsByOperator', () => {
  it('按算子分组；组间按最新运行倒序、组内按创建时间倒序', () => {
    const groups = groupRunsByOperator([
      run({ run_id: 'opA-1', operator: 'opA', created_at: '2026-09-01T10:00:00Z' }),
      run({ run_id: 'opB-1', operator: 'opB', created_at: '2026-09-05T10:00:00Z' }),
      run({ run_id: 'opA-2', operator: 'opA', created_at: '2026-09-03T10:00:00Z' }),
    ])
    expect(groups.map(g => g.operator)).toEqual(['opB', 'opA'])
    expect(groups[1].runs.map(r => r.run_id)).toEqual(['opA-2', 'opA-1'])
    expect(groups[1].latest.run_id).toBe('opA-2')
  })

  it('parse_error 的运行不进分组', () => {
    const groups = groupRunsByOperator([
      run({ run_id: 'broken-1', parse_error: '缺少 run_state.json' }),
      run({ run_id: 'ok-1', operator: 'ok', created_at: '2026-09-01T10:00:00Z' }),
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].runs.map(r => r.run_id)).toEqual(['ok-1'])
  })

  it('缺失/无法解析的 created_at 排在组内最后', () => {
    const groups = groupRunsByOperator([
      run({ run_id: 'a-1', operator: 'op', created_at: 'not-a-date' }),
      run({ run_id: 'a-2', operator: 'op' }),
      run({ run_id: 'a-3', operator: 'op', created_at: '2026-09-02T00:00:00Z' }),
    ])
    expect(groups[0].runs.map(r => r.run_id)).toEqual(['a-3', 'a-1', 'a-2'])
  })
})

describe('operatorOf', () => {
  it('优先用后端派生的 operator', () => {
    expect(operatorOf(run({ run_id: 'dir-name', operator: 'aclnnFoo' }))).toBe('aclnnFoo')
  })

  it('operator 缺失时按目录名从右去掉 日期/时间/微秒 三段', () => {
    expect(operatorOf(run({ run_id: 'aclnnFoo-20260929-091521-532673' }))).toBe('aclnnFoo')
  })

  it('段数不足时退回完整目录名', () => {
    expect(operatorOf(run({ run_id: 'aclnnFoo' }))).toBe('aclnnFoo')
  })
})

describe('pickDefaultRun', () => {
  it('取最新的非 parse_error 运行', () => {
    const got = pickDefaultRun([
      run({ run_id: 'old', created_at: '2026-09-01T00:00:00Z' }),
      run({ run_id: 'broken', created_at: '2026-09-09T00:00:00Z', parse_error: 'x' }),
      run({ run_id: 'new', created_at: '2026-09-05T00:00:00Z' }),
    ])
    expect(got?.run_id).toBe('new')
  })

  it('全部不可解析时返回 null', () => {
    expect(pickDefaultRun([run({ run_id: 'b', parse_error: 'x' })])).toBeNull()
    expect(pickDefaultRun([])).toBeNull()
  })
})

describe('time formatting', () => {
  it('fmtTaskTime 输出本地时区 MM-DD HH:mm；无时区标记按 UTC', () => {
    expect(fmtTaskTime('2026-09-29T16:15:21+00:00')).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(fmtTaskTime('2026-09-29T16:15:21')).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    // 带时区与不带时区（补 Z）应得到同一时刻
    expect(fmtTaskTime('2026-09-29T16:15:21Z')).toBe(fmtTaskTime('2026-09-29T16:15:21'))
    // 无法解析：原样返回；缺失：—
    expect(fmtTaskTime('not-a-date')).toBe('not-a-date')
    expect(fmtTaskTime(undefined)).toBe('—')
  })

  it('fmtTaskOption 拼出 时间 · 状态 · 第X/Y轮', () => {
    const label = fmtTaskOption(run({
      run_id: 'x-1', created_at: '2026-09-29T16:15:21Z', state: 'EXTRACT', current_iteration: 1, max_iterations: 5,
    }))
    expect(label).toMatch(/^\d{2}-\d{2} \d{2}:\d{2} · EXTRACT · 第1\/5轮$/)
  })
})

describe('stateTagType', () => {
  it('与历史抽屉同判：SUCCESS 绿、STOP/BLOCKED 红、MAX_ITERATIONS 橙、进行态蓝、缺失灰', () => {
    expect(stateTagType('SUCCESS')).toBe('success')
    expect(stateTagType('STOPPED_BY_USER')).toBe('danger')
    expect(stateTagType('BLOCKED')).toBe('danger')
    expect(stateTagType('MAX_ITERATIONS')).toBe('warning')
    expect(stateTagType('EXTRACT')).toBe('primary')
    expect(stateTagType(undefined)).toBe('info')
  })
})
