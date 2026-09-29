/**
 * 交接图核心（纯函数，供 vitest 行为测试）：
 *
 * 1. 事件生成节点；**一个事件一个节点**——同一角色的复检/重试是新的处理实例，
 *    各自独立成节点（A→B→A→B→C = 五个节点，绝不折叠后重复画线）。
 * 2. 只有事件中明确给出的 to_agent 才生成箭头——相邻排列不构成交接证据。
 *    目标角色有多个实例时，边指向源事件**之后**的第一个实例（前向推进），
 *    其后才回退到首个实例（表达 A→B 后 B 打回 A 的回边）。
 * 3. 无 to_agent 的节点按事件自身结论独立显示：
 *    passed→独立完成节点、rejected→独立失败节点、running/unconfirmed/skipped 各自独立。
 * 4. 明确交接边不能自动证明接收角色已完成——接收节点的状态仍取共享判据的结论。
 * 5. 跨轮交接（to_round）在本轮事件集中无实例时不落边，由视图层出口节点表达。
 */

export type NodeAction =
  | 'passed' | 'rejected' | 'running' | 'unconfirmed' | 'skipped' | 'handoff' | 'terminal'

export interface REvent {
  iteration: number | null
  agent: string
  action: NodeAction
  to_agent?: string
  to_round?: number
  basis?: string
  at?: string | null
  inferred?: boolean
  [key: string]: unknown
}

export interface GNode {
  id: number
  agent: string
  round: number
  action: NodeAction
  basis: string
  at: string | null
  inferred: boolean
  extras: Record<string, unknown>
  event: REvent
}

export interface GLink {
  from: number
  to: number
  kind: 'handoff'
}

export interface Graph {
  nodes: GNode[]
  links: GLink[]
}

/** 一个事件一个节点；节点保留自身事件引用（详情/时间轴直接消费）。 */
export function buildGraph(events: REvent[]): Graph {
  const nodes: GNode[] = []
  const usable = events.filter(
    e => e.agent !== 'run' && e.action !== 'terminal' && e.iteration !== null && e.iteration !== undefined,
  )
  for (const e of usable) {
    const node: GNode = {
      id: nodes.length,
      agent: e.agent,
      round: e.iteration as number,
      action: e.action,
      basis: String(e.basis ?? ''),
      at: (e.at as string) ?? null,
      inferred: e.inferred !== false,
      extras: {},
      event: e,
    }
    for (const key of ['application', 'origins', 'decisions']) {
      if (e[key] !== undefined) node.extras[key] = e[key]
    }
    nodes.push(node)
  }

  const links: GLink[] = []
  const seenLinks = new Set<string>()
  for (let i = 0; i < usable.length; i++) {
    const e = usable[i]
    if (!e.to_agent) continue
    const targetRound = e.to_round ?? e.iteration
    const candidates = nodes
      .map((n, idx) => ({ n, idx }))
      .filter(({ n }) => n.agent === e.to_agent && n.round === targetRound)
    if (!candidates.length) continue
    // 前向推进：源之后的首个实例；无（如打回上游）则回退到首个实例
    const target = candidates.find(({ idx }) => idx > i) || candidates[0]
    const linkKey = `${i}->${target.idx}`
    if (seenLinks.has(linkKey)) continue
    seenLinks.add(linkKey)
    links.push({ from: i, to: target.idx, kind: 'handoff' })
  }
  return { nodes, links }
}

/** 供外部消费的判定：接收角色状态来自共享判据，边不改变它。 */
export function receiverStatus(graph: Graph, link: GLink): NodeAction {
  const target = graph.nodes.find(n => n.id === link.to)
  return (target?.action ?? 'unconfirmed') as NodeAction
}

export interface ExitHandoff {
  agent: string
  fromAgent: string
  toRound: number
  fromRound: number
  basis: string
}

/** 跨轮交接（视图级出口）：当前轮中 to_round 指向后续轮的事件，
 * 在本轮末尾出口节点呈现、在目标轮以来源标注开场承接。 */
export function exitHandoffs(events: REvent[], round: number): ExitHandoff[] {
  const out: ExitHandoff[] = []
  const seen = new Set<string>()
  for (const e of events) {
    if (e.iteration !== round) continue
    const toRound = e.to_round
    if (!e.to_agent || toRound === undefined || toRound === e.iteration) continue
    const key = `${e.agent}->${e.to_agent}@${toRound}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({
      agent: e.to_agent,
      fromAgent: e.agent,
      toRound,
      fromRound: e.iteration,
      basis: e.basis || '',
    })
  }
  return out
}

/** 目标轮的开场来源：上一轮交入本角色的交接（无则 null）。 */
export function entrySource(events: REvent[], round: number, agent: string): ExitHandoff | null {
  for (const e of events) {
    if (e.to_agent === agent && e.to_round === round && e.iteration === round - 1) {
      return {
        agent,
        fromAgent: e.agent,
        toRound: round,
        fromRound: e.iteration,
        basis: e.basis || '',
      }
    }
  }
  return null
}
