<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { laneWidth, laneBoundary, laneCenter } from './flow-layout'
import NodeDetailPanel from './NodeDetailPanel.vue'
import type { ReplayEvent } from '../../api/types'

/**
 * 轮次与交接 · 泳道图（demo.html 布局范式）：
 * 顶部 sticky 角色列 → 时间流向下，节点=处理实例，四色箭头=交接/退回/异常/通过。
 * 点击节点 → 右侧 sticky 详情栏；轮次 tabs 过滤；悬停 tooltip 预览。
 */
const props = defineProps<{
  runId: string
  runView: any
  engineers: any[]
  events: ReplayEvent[]
  round: number
  scrollLeft: number
  zoom: number
  viewport: number
}>()

const emit = defineEmits<{ (e: 'scroll-x', left: number): void; (e: 'update:zoom', zoom: number): void; (e: 'select-agent', name: string | null): void }>()

// ---------- 布局常量（与 demo 紧凑版一致） ----------
const LABEL_W = 130
const LANE_W = computed(() => laneWidth(props.viewport, lanes.value.length, props.zoom))
const NODE_W = 136
const NODE_H = 49
const ROW_STEP = 71
const BAND_H = 0
const BAND_GAP = 8
const TEAM_H = 0

// ---------- 缩放（小屏一眼看全泳道） ----------
const zoom = computed({get: () => props.zoom, set: (v: number) => emit('update:zoom', v)})

const lanes = computed(() => props.engineers.filter((e) => e.stage))
const laneIndex = (name: string) => lanes.value.findIndex((e) => e.name === name)
const laneX = (i: number) => laneCenter(i, LANE_W.value)
const canvasWidth = computed(() => LABEL_W + lanes.value.length * LANE_W.value)

// ---------- 事件 → 交接边 ----------
interface Edge {
  from: string
  to: string | null
  kind: 'forward' | 'return' | 'failure' | 'pass' | 'skip'
  round: number
  time: string | null
  basis: string
  inferred: boolean
}

function buildEdges(list: ReplayEvent[]): Edge[] {
  const usable = (list || []).filter((e) => e.agent !== 'run' && e.action !== 'terminal')
  const out: Edge[] = []
  for (let i = 0; i < usable.length; i++) {
    const e = usable[i]
    if (laneIndex(e.agent) < 0) continue
    if (e.action === 'skipped') {
      out.push({ from: e.agent, to: null, kind: 'skip', round: e.iteration || 1, time: e.at || null, basis: e.basis, inferred: e.inferred })
      continue
    }
    const next = usable[i + 1]
    const to = e.to_agent || (next ? next.agent : null)
    if (!to || laneIndex(to) < 0) continue
    let kind: Edge['kind'] = 'forward'
    if (e.action === 'rejected') {
      kind = to === 'failure-analyst' || e.agent === 'case-executor' ? 'failure' : 'return'
    } else if (e.action === 'passed' && !e.to_agent) {
      kind = 'pass'
    } else if (e.action === 'passed' && e.to_agent && !next) {
      kind = 'pass'
    }
    // to 节点轮次：若下一事件就是 to 角色，采用其自身轮次（跨轮交接如 analyst→updater）
    const round = next && next.agent === to && next.iteration ? next.iteration : e.iteration || 1
    out.push({ from: e.agent, to, kind, round, time: e.at || null, basis: e.basis, inferred: e.inferred })
  }
  return out
}

// ---------- 边 → 节点图（链式复用端点，同 demo buildGraph） ----------
interface GNode {
  id: number
  agent: string
  round: number
  incoming: number | null
  outgoing: number | null
  skip?: boolean
}

const edges = computed(() => buildEdges(props.events.filter(e => e.iteration === props.round)))

const graph = computed(() => {
  const nodes: GNode[] = []
  const links: { from: number; to: number; kind: Edge['kind'] }[] = []
  edges.value.forEach((e, i) => {
    let from = nodes[nodes.length - 1]
    if (!from || from.agent !== e.from) {
      from = { id: nodes.length, agent: e.from, round: e.round, incoming: null, outgoing: null }
      nodes.push(from)
    }
    from.outgoing = i
    if (e.to === null) {
      from.skip = true
      return
    }
    const to: GNode = { id: nodes.length, agent: e.to, round: e.round, incoming: i, outgoing: null }
    nodes.push(to)
    links.push({ from: from.id, to: to.id, kind: e.kind })
  })
  return { nodes, links }
})

// ---------- 轮次过滤 ----------
const selectedRound = computed(() => props.round)
const rounds = computed(() => {
  const set = new Set<number>()
  for (const n of graph.value.nodes) set.add(n.round)
  return [...set].sort((a, b) => a - b)
})

const visibleNodes = computed(() =>
  graph.value.nodes.filter((n) => n.round === selectedRound.value),
)

// ---------- 布局 ----------
interface Band {
  round: number
  y: number
}

const layout = computed(() => {
  const bands: Band[] = []
  const positions = new Map<number, { x: number; y: number }>()
  let y = 0
  let last: number | null = null
  for (const node of visibleNodes.value) {
    if (last !== node.round) {
      if (last !== null) y += BAND_GAP
      bands.push({ round: node.round, y })
      y += BAND_H
      last = node.round
    }
    positions.set(node.id, { x: laneX(Math.max(0, laneIndex(node.agent))), y })
    y += ROW_STEP
  }
  return { bands, positions, height: y + 12 }
})

const visibleLinks = computed(() =>
  graph.value.links.filter(
    (l) => layout.value.positions.has(l.from) && layout.value.positions.has(l.to),
  ),
)

// ---------- 节点信息 ----------
function shorten(text: string, max = 26): string {
  if (!text) return ''
  return text.length <= max ? text : text.slice(0, max) + '…'
}

function nodeEdge(node: GNode): Edge | null {
  const idx = node.outgoing ?? node.incoming
  return idx === null ? null : edges.value[idx] || null
}

function nodeKind(node: GNode): Edge['kind'] {
  if (node.skip) return 'skip'
  return nodeEdge(node)?.kind || 'forward'
}

function nodeOutput(node: GNode): string {
  return shorten(nodeEdge(node)?.basis || '')
}

function nodeTime(node: GNode): string {
  const at = nodeEdge(node)?.time
  if (!at) return '时间未记录'
  const d = new Date(at)
  if (isNaN(d.getTime())) return at
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}/${dd} ${hh}:${mi}`
}

function agentDef(name: string) {
  return lanes.value[laneIndex(name)] || { name, role: '', color: 'blue', skills: [] }
}

const activeAgentNames = computed(() => new Set(visibleNodes.value.map((n) => n.agent)))

// ---------- 选中与详情 ----------
const detailAnchor = ref<HTMLElement>()
const selectedId = ref<number | null>(null)
const selectedNode = computed(() => graph.value.nodes.find((n) => n.id === selectedId.value) || null)
const selectedEdge = computed(() => (selectedNode.value ? nodeEdge(selectedNode.value) : null))

function select(id: number) {
  selectedId.value = id
  emit('select-agent', graph.value.nodes.find(n => n.id === id)?.agent || null)
  hideTooltip()
  nextTick(() => detailAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

watch(visibleNodes, (list) => {
  if (!list.some((n) => n.id === selectedId.value)) {
    selectedId.value = null
  }
})

// ---------- Tooltip ----------
const tooltip = ref<{ x: number; y: number; node: GNode } | null>(null)

function hideTooltip() {
  tooltip.value = null
}

// ---------- 横向滚动 ----------
const scroller = ref<HTMLElement>()
watch(() => [props.scrollLeft, props.zoom], async () => {
  await nextTick()
  if (scroller.value && Math.abs(scroller.value.scrollLeft - props.scrollLeft) > 1) scroller.value.scrollLeft = props.scrollLeft
})
function onScroll() { hideTooltip(); if (scroller.value) emit('scroll-x', scroller.value.scrollLeft) }
function selectAgent(name: string) {
  const nodes=visibleNodes.value.filter(n => n.agent === name)
  const node=nodes[nodes.length-1]
  if(!node){selectedId.value=null;return}
  select(node.id)
  const p=layout.value.positions.get(node.id)
  if(p && scroller.value){
    const left=Math.max(0,p.x*props.zoom-scroller.value.clientWidth/2)
    scroller.value.scrollTo({top:Math.max(0,p.y*props.zoom-30),left,behavior:'smooth'})
  }
}
function clearSelection() { selectedId.value = null; emit('select-agent', null) }
defineExpose({ selectAgent, clearSelection })
watch(() => props.round, async () => { hideTooltip(); selectedId.value = null; await nextTick(); scroller.value?.scrollTo({ top: 0, behavior: 'instant' }); })
function scrollBy(dx: number) {
  scroller.value?.scrollBy({ left: dx, behavior: 'smooth' })
}

const KIND_TEXT: Record<string, string> = {
  forward: '交接',
  return: '退回',
  failure: '异常',
  pass: '通过',
  skip: '跳过',
}
</script>

<template>
  <div class="handoff">
    <!-- 控制行：轮次 tabs + 计数 + 图例 -->
    <div v-show="false" class="controls">
      <span class="hint">{{ visibleNodes.length }} 个处理节点 · {{ activeAgentNames.size }} 个参与角色</span>
      <div class="legend">
        <span><i class="dot dot-forward" />交接</span>
        <span><i class="dot dot-return" />退回</span>
        <span><i class="dot dot-failure" />异常</span>
        <span><i class="dot dot-pass" />通过</span>
        <span><i class="dot dot-skip" />未参与 / 无记录</span>
      </div>
    </div>

    <div class="layout">
      <!-- 泳道图板 -->
      <section class="board" aria-label="智能体交接图">
        <div class="board-head">
          <span v-show="false" class="hint">← 左右滚动查看所有角色 · 悬停看摘要 · 点击节点看产出与依据</span>
          <div class="board-tools">
            <div class="zoom-controls" role="group" aria-label="缩放">
              <button
                v-for="z in [0.5, 0.75, 1]"
                :key="z"
                :class="{ on: zoom === z }"
                @click="zoom = z"
              >
                {{ z * 100 }}%
              </button>
            </div>
            <div class="scroll-controls">
              <button class="arrow-button" aria-label="向左滚动" @click="scrollBy(-462)">←</button>
              <button class="arrow-button" aria-label="向右滚动" @click="scrollBy(462)">→</button>
            </div>
          </div>
        </div>
        <div ref="scroller" class="scroller" tabindex="0" @scroll="onScroll">
          <div
            class="zoom-box"
            :style="{ width: canvasWidth * zoom + 'px', height: (TEAM_H + layout.height) * zoom + 'px' }"
          >
          <div class="canvas" :style="{ width: canvasWidth + 'px', transform: `scale(${zoom})` }">
            <!-- 图区 -->
            <div class="graph" :style="{ height: layout.height + 'px', width: canvasWidth + 'px' }">
              <div v-for="(lane, index) in lanes" :key="lane.name" class="lane-guide" :style="{ left: laneBoundary(index, LANE_W) + 'px' }" />
              <svg class="graph-svg" :width="canvasWidth" :height="layout.height" aria-hidden="true">
                <defs>
                  <marker v-for="k in ['forward', 'return', 'failure', 'pass']" :id="`arrow-${k}`" :key="k"
                    viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto">
                    <path d="M0 0 L10 5 L0 10" :class="`marker-${k}`" />
                  </marker>
                </defs>
                <path
                  v-for="(l, i) in visibleLinks"
                  :key="i"
                  :d="`M${layout.positions.get(l.from)!.x} ${layout.positions.get(l.from)!.y + NODE_H} V${(layout.positions.get(l.from)!.y + NODE_H + layout.positions.get(l.to)!.y - 3) / 2} H${layout.positions.get(l.to)!.x} V${layout.positions.get(l.to)!.y - 3}`"
                  :class="`edge edge-${l.kind}`"
                  :marker-end="`url(#arrow-${l.kind})`"
                />
              </svg>

              <!-- 节点 -->
              <template v-for="node in visibleNodes" :key="node.id">
                <div
                  class="activity-time"
                  :style="{ top: layout.positions.get(node.id)!.y + 9 + 'px' }"
                >
                  <b>{{ nodeTime(node) }}</b>
                </div>
                <button
                  class="activity"
                  :class="[`kind-${nodeKind(node)}`, { chosen: selectedId === node.id }]"
                  :style="{
                    left: layout.positions.get(node.id)!.x - NODE_W / 2 + 'px',
                    top: layout.positions.get(node.id)!.y + 'px',
                    width: NODE_W + 'px',
                    height: NODE_H + 'px',
                  }"
                  :aria-label="`${agentDef(node.agent).name}：${nodeOutput(node)}`"
                  :aria-pressed="selectedId === node.id"
                  @click="select(node.id)"
                >
                  <strong>{{ agentDef(node.agent).role || node.agent }}</strong>
                  <small>{{ nodeOutput(node) }}</small>
                </button>
              </template>
            </div>
          </div>
          </div>
        </div>
        <div class="board-foot">
          每个处理实例只出现一次；复检或重试是新的处理实例。状态与交接由产物推导，详情栏带「推导」标记。
        </div>
      </section>

    </div>

    <section ref="detailAnchor" class="unified-detail" aria-label="处理详情">
      <div class="detail-toolbar"><span>处理详情 · 输入、技能知识、产物与交接依据</span><button v-if="selectedNode" @click="clearSelection">收起详情</button></div>
      <NodeDetailPanel
        :run-id="runId"
        :run-view="runView"
        :node="selectedNode"
        :edge="selectedEdge"
        :engineer="selectedNode ? agentDef(selectedNode.agent) : null"
        :kind-text="selectedNode ? KIND_TEXT[nodeKind(selectedNode)] : ''"
      />
    </section>

  </div>
</template>

<style scoped>
.controls { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.tabs { background: var(--wb-line); padding: 4px; border-radius: 9px; display: flex; gap: 4px; }
.tabs button { border: 0; background: none; color: var(--wb-muted); padding: 7px 18px; border-radius: 6px; }
.tabs button.selected { color: var(--wb-blue); background: var(--wb-card); box-shadow: var(--wb-shadow); font-weight: 650; }
.hint { font-size: 12px; color: var(--wb-muted); }
.legend { margin-left: auto; display: flex; gap: 16px; font-size: 12px; color: var(--wb-muted); }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
.dot-forward { background: var(--wb-blue); }
.dot-return { background: var(--wb-orange); }
.dot-failure { background: var(--wb-red); }
.dot-pass { background: var(--wb-green); }
.dot-skip { background: var(--wb-faint); }

.layout { display: grid; grid-template-columns: minmax(0, 1fr) clamp(260px, 24vw, 330px); gap: 18px; align-items: start; }
.board { background: var(--wb-card); border: 1px solid var(--wb-line); border-radius: 14px; overflow: hidden; }
.board-head {
  padding: 13px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--wb-card-soft);
  border-bottom: 1px solid var(--wb-line);
  flex-wrap: wrap;
}
.board-tools { display: flex; gap: 10px; align-items: center; }
.zoom-controls { display: flex; gap: 3px; background: var(--wb-line); padding: 3px; border-radius: 7px; }
.zoom-controls button {
  border: 0;
  background: none;
  color: var(--wb-muted);
  padding: 3px 9px;
  border-radius: 5px;
  font-size: 11px;
}
.zoom-controls button.on { background: var(--wb-card); color: var(--wb-blue); font-weight: 650; box-shadow: var(--wb-shadow); }
.scroll-controls { display: flex; gap: 7px; }
.arrow-button { border: 1px solid var(--wb-line); border-radius: 6px; background: var(--wb-card); color: var(--wb-blue); width: 33px; height: 29px; }
.scroller { overflow: auto; max-height: max(180px, calc(100dvh - 340px)); }
.zoom-box { position: relative; }
.canvas { position: absolute; top: 0; left: 0; transform-origin: top left; }

.team {
  position: sticky;
  top: 0;
  z-index: 4;
  display: grid;
  height: 96px;
  background: var(--wb-card);
  box-shadow: 0 2px 9px rgba(35, 59, 85, 0.05);
  border-bottom: 1px solid var(--wb-line);
}
.team-label { padding: 15px; font-size: 12px; font-weight: 650; display: flex; flex-direction: column; justify-content: center; }
.team-label small { font-weight: 400; color: var(--wb-muted); margin-top: 4px; font-size: 10px; }
.person { padding: 8px 5px; height: 96px; overflow: hidden; text-align: center; border-left: 1px solid var(--wb-line-soft); position: relative; transition: filter 0.2s, opacity 0.2s; }
.person.off { filter: grayscale(1); opacity: 0.38; }
.person.highlight { background: var(--wb-blue-soft); box-shadow: inset 0 -3px var(--wb-blue); }
.person b { display: block; font-size: 10.5px; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.person small { display: block; font-size: 9px; color: var(--wb-muted); }
.participation { position: absolute; right: 9px; top: 10px; width: 6px; height: 6px; border-radius: 50%; background: var(--wb-faint); }
.participation.on { background: var(--wb-green); }

.graph { position: relative; }
.lane-guide { position: absolute; top: 0; bottom: 0; width: 0; border-left: 1px solid var(--wb-line); pointer-events: none; }
.graph-svg { position: absolute; inset: 0; pointer-events: none; z-index: 1; }
.edge { fill: none; stroke-width: 1.6; stroke-linejoin: round; }
.edge-forward { stroke: var(--wb-blue); }
.edge-return { stroke: var(--wb-orange); }
.edge-failure { stroke: var(--wb-red); }
.edge-pass { stroke: var(--wb-green); }
.marker-forward { fill: var(--wb-blue); }
.marker-return { fill: var(--wb-orange); }
.marker-failure { fill: var(--wb-red); }
.marker-pass { fill: var(--wb-green); }

.round-band {
  position: absolute;
  left: 0;
  right: 0;
  padding: 0 14px;
  background: var(--wb-band-bg);
  z-index: 2;
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--wb-line);
}
.round-band strong { font-size: 13px; }

.activity-time {
  position: absolute;
  left: 12px;
  width: 110px;
  color: var(--wb-muted);
  font-size: 9px;
  line-height: 1.5;
  z-index: 2;
}
.activity-time b { font-weight: 500; color: var(--wb-muted); }

.activity {
  position: absolute;
  border: 1px solid var(--wb-blue-line);
  border-radius: 8px;
  background: var(--wb-card);
  padding: 6px 9px;
  text-align: left;
  z-index: 3;
  color: var(--wb-ink);
  box-shadow: var(--wb-shadow);
  transition: box-shadow 0.15s, border-color 0.15s;
}
.activity strong { font-size: 11px; display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.activity small { font-size: 10px; color: var(--wb-muted); display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.activity:hover { box-shadow: 0 4px 14px rgba(39, 116, 237, 0.15); border-color: var(--wb-blue); }
.activity.chosen { border: 2px solid var(--wb-blue); padding: 5px 8px; box-shadow: 0 0 0 3px rgba(39, 116, 237, 0.1); }
.activity.kind-return { background: var(--wb-orange-soft); border-color: var(--wb-orange-line); }
.activity.kind-failure { background: var(--wb-red-soft); border-color: var(--wb-red-line); }
.activity.kind-pass { background: var(--wb-green-soft); border-color: var(--wb-green-line); }
.activity.kind-skip { background: var(--wb-code-bg); border-color: var(--wb-line); opacity: 0.75; }
.activity.chosen { border-color: var(--wb-blue); }

.board-foot { padding: 10px 16px; color: var(--wb-muted); font-size: 11px; background: var(--wb-card-soft); border-top: 1px solid var(--wb-line); }

.tooltip {
  position: fixed;
  z-index: 30;
  max-width: min(300px, 82vw);
  width: 280px;
  background: #172d49;
  color: #fff;
  border-radius: 9px;
  box-shadow: 0 8px 28px rgba(21, 43, 73, 0.2);
  padding: 12px 14px;
  font-size: 11px;
  pointer-events: none;
}
.tooltip b { display: block; font-size: 12px; margin-bottom: 4px; }
.tooltip p { margin: 3px 0; color: #dbe6f5; }
.tip-foot { font-size: 10px; color: #a7beda; margin-top: 7px; }

@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .legend { display: none; }
  .board-head .hint { flex-basis: 100%; order: 2; }
}
@media (max-width: 600px) {
  .controls { gap: 8px; }
  .tabs { overflow-x: auto; max-width: 100%; }
  .tabs button { padding: 7px 12px; white-space: nowrap; }
}
/* 触屏：原生滑动已够，隐藏滚动按钮；节点热区已由 NODE_H=49 保证 >44px */
@media (pointer: coarse) {
  .scroll-controls { display: none; }
}
/* 让流程紧接共享头像行；工具移至图底，不占用两者之间的空间。 */
.board { display: flex; flex-direction: column; border-top: 0; border-top-left-radius: 0; border-top-right-radius: 0; }
.scroller { order: 0; }
.board-head { order: 1; padding: 6px 12px; border-top: 1px solid var(--wb-line); border-bottom: 0; justify-content: flex-end; }
.board-foot { order: 2; }
.layout { display:block; }
.board { border-left:0; border-right:0; border-radius:0; }
.scroller { scrollbar-width:none; }.scroller::-webkit-scrollbar{display:none}
.unified-detail { margin:16px; scroll-margin-top:230px; }
.detail-toolbar { display:flex; justify-content:space-between; align-items:center; margin:0 0 10px; font-size:12px; color:var(--wb-muted); }
.detail-toolbar button { border:1px solid var(--wb-line); border-radius:6px; background:var(--wb-card); color:var(--wb-blue); padding:6px 12px; cursor:pointer; }
</style>
