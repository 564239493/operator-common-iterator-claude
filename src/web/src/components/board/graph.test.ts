import { describe, expect, it } from 'vitest'
import { buildGraph, receiverStatus, exitHandoffs, entrySource, type REvent } from './graph'

const ev = (agent: string, action: REvent['action'], extra: Partial<REvent> = {}): REvent => ({
  iteration: 2, agent, action, basis: `${agent} ${action}`, inferred: true, ...extra,
})

describe('buildGraph', () => {
  it('相邻两条均无 to_agent 的完成事件 → 两个独立节点、零条边', () => {
    const g = buildGraph([
      ev('source-analyst', 'passed', { to_agent: undefined }),
      ev('scene-scanner', 'passed', { to_agent: undefined }),
    ])
    expect(g.nodes).toHaveLength(2)
    expect(g.links).toHaveLength(0)
  })

  it('只有明确的 to_agent 生成箭头；相邻排列不构成交接证据', () => {
    const g = buildGraph([
      ev('case-generator', 'passed'),               // 无 to_agent：相邻也不连
      ev('case-executor', 'passed', { to_agent: 'quality-reviewer' }),
      ev('quality-reviewer', 'passed'),
    ])
    expect(g.links).toHaveLength(1)
    expect(g.links[0].kind).toBe('handoff')
  })

  it('passed 且无 to_agent → 独立完成节点（不因没有箭头而消失）', () => {
    const g = buildGraph([ev('source-analyst', 'passed')])
    expect(g.nodes).toHaveLength(1)
    expect(g.nodes[0].action).toBe('passed')
  })

  it('rejected 且无 to_agent → 独立失败节点', () => {
    const g = buildGraph([ev('quality-reviewer', 'rejected')])
    expect(g.nodes[0].action).toBe('rejected')
  })

  it('running / unconfirmed / skipped 节点独立显示且不落边', () => {
    const g = buildGraph([
      ev('case-generator', 'running'),
      ev('constraint-updater', 'unconfirmed'),
      ev('constraint-repairer', 'skipped'),
    ])
    expect(g.nodes.map(n => n.action).sort()).toEqual(['running', 'skipped', 'unconfirmed'])
    expect(g.links).toHaveLength(0)
  })

  it('明确交接边不能自动证明接收角色已完成——接收方状态取自身事件', () => {
    const g = buildGraph([
      ev('case-generator', 'passed', { to_agent: 'case-executor' }),
      ev('case-executor', 'unconfirmed'), // 接收方自己只有待确认证据
    ])
    expect(g.links).toHaveLength(1)
    expect(receiverStatus(g, g.links[0])).toBe('unconfirmed')
  })

  it('跨轮交接仅当目标节点存在时落边', () => {
    const g = buildGraph([
      ev('failure-analyst', 'passed', { to_agent: 'constraint-updater', to_round: 3 }),
      // 本轮事件集中没有 round 3 的 updater 事件 → 不造幻影节点
    ])
    expect(g.nodes).toHaveLength(1)
    expect(g.links).toHaveLength(0)
  })

  it('检查→修复→复检→修复→复检→生成：每个处理实例独立成节点（不折叠重复画线）', () => {
    // 对应真实业务：检查①退回修复 → 修复①交复检 → 检查②仍有问题退回 → 修复②交复检 → 检查③通过交生成
    const check1 = ev('constraint-checker', 'rejected', { to_agent: 'constraint-repairer' })
    const repair1 = ev('constraint-repairer', 'passed', { to_agent: 'constraint-checker' })
    const check2 = ev('constraint-checker', 'rejected', { to_agent: 'constraint-repairer' })
    const repair2 = ev('constraint-repairer', 'passed', { to_agent: 'constraint-checker' })
    const check3 = ev('constraint-checker', 'passed', { to_agent: 'case-generator' })
    const g = buildGraph([check1, repair1, check2, repair2, check3])
    expect(g.nodes).toHaveLength(5)
    expect(g.nodes.map(n => n.agent)).toEqual([
      'constraint-checker', 'constraint-repairer', 'constraint-checker',
      'constraint-repairer', 'constraint-checker',
    ])
    expect(g.links).toHaveLength(4)
    // 前向推进：检查①→修复①、修复①→检查②（而非折回检查①）、检查②→修复②、修复②→检查③
    const pairs = g.links.map(l => [l.from, l.to])
    expect(pairs).toEqual([[0, 1], [1, 2], [2, 3], [3, 4]])
  })

  it('同一角色的多次处理实例保持独立节点（复检是新的实例）', () => {
    const g = buildGraph([
      ev('constraint-checker', 'rejected', { basis: '发现 2 项未解决', to_agent: 'constraint-repairer' }),
      ev('constraint-repairer', 'passed', { basis: '修复 2 项', to_agent: 'constraint-checker' }),
      ev('constraint-checker', 'rejected', { basis: '仍有 1 项未解决', to_agent: 'constraint-repairer' }),
      ev('constraint-repairer', 'passed', { basis: '修复 1 项', to_agent: 'constraint-checker' }),
      ev('constraint-checker', 'passed', { basis: '复检通过', to_agent: 'case-generator' }),
    ])
    expect(g.nodes.filter(n => n.agent === 'constraint-checker')).toHaveLength(3)
    expect(g.nodes.filter(n => n.agent === 'constraint-repairer')).toHaveLength(2)
    // 回边指向源之后的下一个检查实例（B1→A2、B2→A3），最终检查通过交给生成
    const checkerIds = g.nodes.map((n, i) => n.agent === 'constraint-checker' ? i : -1).filter(i => i >= 0)
    const repairLinks = g.links.filter(l => g.nodes[l.from].agent === 'constraint-repairer')
    expect(repairLinks.map(l => l.to)).toEqual([checkerIds[1], checkerIds[2]])
  })

  it('打回上游：源之后无该角色实例时，边回退到首个实例', () => {
    const g = buildGraph([
      ev('constraint-checker', 'passed', { to_agent: 'case-generator' }),
      ev('constraint-repairer', 'passed', { to_agent: 'constraint-checker' }),
      ev('case-generator', 'passed'),
    ])
    expect(g.links).toHaveLength(2)
    const back = g.links.find(l => g.nodes[l.from].agent === 'constraint-repairer')!
    expect(g.nodes[back.to].agent).toBe('constraint-checker') // 首个（也是唯一）实例
  })

  it('terminal 与 run 级事件不生成节点', () => {
    const g = buildGraph([
      { iteration: null, agent: 'run', action: 'terminal', basis: '终态' },
      ev('case-executor', 'passed'),
    ])
    expect(g.nodes).toHaveLength(1)
    expect(g.nodes[0].agent).toBe('case-executor')
  })

  it('额外展示字段（application/origins/decisions）随节点保留', () => {
    const g = buildGraph([
      ev('constraint-supplementer', 'passed', { application: '非空补丁应用结果待确认' }),
    ])
    expect(g.nodes[0].extras.application).toBe('非空补丁应用结果待确认')
  })
})

describe('exitHandoffs / entrySource（跨轮交接出口与来源）', () => {
  const events: REvent[] = [
    ev('failure-analyst', 'passed', { to_agent: 'constraint-updater', to_round: 3 }),
    ev('constraint-updater', 'passed', { to_agent: 'constraint-checker' }),
  ]

  it('第 2 轮视图：诊断交下一轮更新 → 出口交接（agent=更新员，toRound=3）', () => {
    const exits = exitHandoffs(events, 2)
    expect(exits).toHaveLength(1)
    expect(exits[0]).toMatchObject({ agent: 'constraint-updater', fromAgent: 'failure-analyst', toRound: 3, fromRound: 2 })
  })

  it('同轮 to_round 不算跨轮出口', () => {
    expect(exitHandoffs(events.slice(1), 2)).toHaveLength(0)
  })

  it('第 3 轮视图：更新员的开场来源 = 第 2 轮诊断', () => {
    const src = entrySource(events, 3, 'constraint-updater')
    expect(src).toMatchObject({ fromAgent: 'failure-analyst', fromRound: 2 })
    expect(entrySource(events, 3, 'case-generator')).toBeNull()
  })
})
