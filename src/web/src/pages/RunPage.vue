<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { usePolling } from '../composables/usePolling'
import { useEngineers } from '../composables/useEngineers'
import { useTheme } from '../composables/useTheme'
import AgentBand from '../components/band/AgentBand.vue'
import HandoffBoard from '../components/board/HandoffBoard.vue'
import HistoryDrawer from '../components/history/HistoryDrawer.vue'
import type { ReplayEvent } from '../api/types'

/** 单页：顶部轮次汇总、角色队列、交接图及统一详情。 */
const props = defineProps<{ runId: string }>()
const router = useRouter()
const { theme, toggle } = useTheme()
const historyDrawer = ref<InstanceType<typeof HistoryDrawer>>()
const flowBoard = ref<InstanceType<typeof HandoffBoard>>()
const flowScroll = ref(0)
const flowZoom = ref(1)
const flowViewport = ref(0)
const snapRef = ref<HTMLElement>()
const selectedRound = ref<number | null>(null)

const selectedAgent = ref<string | null>(null)

const { data: runView, error } = usePolling<any>(
  () => api.runView(props.runId),
  (data) => (data?.is_terminal ? 10000 : 2000),
)
const { data: replayEvents } = usePolling<ReplayEvent[]>(
  () => api.replay(props.runId),
  () => (runView.value?.is_terminal ? 15000 : 5000),
)

const { engineers } = useEngineers(runView)


watch(
  () => props.runId,
  () => {
    selectedAgent.value = null
    snapRef.value?.scrollTo({ top: 0, behavior: 'instant' })
    selectedRound.value = null
  },
)

function onAgentSelect(name: string | null) {
  selectedAgent.value = name
  if (name) flowBoard.value?.selectAgent(name)
  else flowBoard.value?.clearSelection()
}

// 单一轮次上下文；刷新不覆盖用户正在查看的历史轮次。
const rounds = computed<number[]>(() => (runView.value?.iterations || []).map((v: any) => Number(v.n)).sort((a: number,b: number) => a-b))
watch(rounds, list => {
  if (!list.length) return
  if (selectedRound.value === null || !list.includes(selectedRound.value)) {
    const current = Number(runView.value?.current_iteration)
    selectedRound.value = list.includes(current) ? current : list[list.length - 1]
  }
}, { immediate: true })
// 消费后端共享判据的带轮次状态（progress_rules.per_round_states）：
// "本轮无记录"不得解释为未参与——未执行阶段为 pending，证据不足为 unconfirmed；
// 不在客户端另写一套规则。
const roundEngineers = computed(() => engineers.value.map(eng => {
  const state = (runView.value?.round_states || {})[selectedRound.value]?.[eng.name]
  const status = state?.status ?? 'pending'
  return { ...eng, runtime: { ...eng.runtime, ...state, status,
    iteration: selectedRound.value,
    basis: state?.basis || '尚未执行到该阶段（推导）', inferred: true } }
}))
const nextRound = computed(() => {
  const index = rounds.value.indexOf(selectedRound.value ?? -1)
  return index >= 0 ? rounds.value[index + 1] ?? null : null
})
const previousRound = computed(() => {
  const index = rounds.value.indexOf(selectedRound.value ?? -1)
  return index > 0 ? rounds.value[index - 1] : null
})
const roundItems = computed(() => rounds.value.map(n => {
  const iteration = (runView.value?.iterations || []).find((v: any) => Number(v.n) === n)
  const execution = iteration?.execution
  const action = iteration?.analysis?.overall_action
  const result = execution?.verdict === 'engine_error' ? '执行引擎故障'
    : execution && Number(execution.total) > 0 ? '执行通过 ' + execution.passed + '/' + execution.total
    : '暂无执行结果'
  const detail = action === 'UPDATE_CONSTRAINTS' ? '诊断建议修正约束'
    : action === 'MIXED_FAILURE_REVIEW' ? '诊断建议人工复核'
    : iteration?.quality_gate?.next_state === 'SUCCESS' ? '本轮评审通过'
    : n === Number(runView.value?.current_iteration) && !runView.value?.is_terminal ? '当前业务轮次'
    : '查看过程与依据'
  return { n, result, detail }
}))
watch(selectedRound, () => { selectedAgent.value = null })

const operatorName = computed(() => {
  const doc = runView.value?.operator_doc || ''
  const base = doc.replace(/\\/g, '/').split('/').pop() || ''
  return base.replace(/\.md$/i, '') || props.runId
})

const metadata = computed(() => {
  const v = runView.value
  if (!v) return ''
  const parts: string[] = []
  if (v.scene?.enabled && (v.scene.device_types || []).length) parts.push(v.scene.device_types.join('、'))
  if (v.test_framework) parts.push(String(v.test_framework).toUpperCase())
  if (v.mode) parts.push(v.mode === 'real' ? '真实执行' : v.mode)
  return parts.join('　·　')
})

// 等待用户的挂起态：非终态（与后端 config.WAITING_STATES 对齐），用户答复后流程继续
const WAITING_STATES = new Set([
  'MIXED_FAILURE_REVIEW',
  'NEEDS_HUMAN_EVIDENCE',
  'AWAITING_HUMAN_CONSTRAINTS',
  'HUMAN_CHECKPOINT',
])

const STATUS_CHIP: Record<string, { text: string; cls: string }> = {
  SUCCESS: { text: '✓ 流程已完成', cls: 'ok' },
  MAX_ITERATIONS: { text: '◷ 达到最大轮数', cls: 'warn' },
  BLOCKED: { text: '■ 产物校验阻断', cls: 'bad' },
  STOPPED_BY_USER: { text: '■ 人工停止', cls: 'bad' },
  STOP_GENERATOR_BUG: { text: '■ 生成器缺陷止损', cls: 'bad' },
  STOP_EXECUTOR_BUG: { text: '■ 执行器缺陷止损', cls: 'bad' },
  // 挂起等待态：非终态（用户答复后流程继续），页面保持正常刷新
  MIXED_FAILURE_REVIEW: { text: '◷ 等待人工决定（成败混合评审）', cls: 'warn' },
  NEEDS_HUMAN_EVIDENCE: { text: '◷ 等待人工补充证据', cls: 'warn' },
  AWAITING_HUMAN_CONSTRAINTS: { text: '◷ 等待人工补充约束', cls: 'warn' },
  HUMAN_CHECKPOINT: { text: '◷ 等待人工检查点', cls: 'warn' },
}
const statusChip = computed(() => {
  const s = runView.value?.state
  return STATUS_CHIP[s] || { text: `● ${s || '…'}`, cls: 'run' }
})

const metrics = computed(() => {
  const v = runView.value
  if (!v) return []
  const exeIt = iterFor('execution')
  const anaIt = iterFor('analysis')
  const exe = exeIt?.execution
  const g = iterFor('quality_gate')?.quality_gate
  return [

    {
      icon: '✓',
      label: '执行结果',
      value: exe ? `${exe.passed} / ${exe.total}` : '—',
      unit: exe
        ? exe.verdict === 'full_pass' ? '全部通过'
        : exe.verdict === 'partial_pass' ? `失败 ${exe.failed} 条`
        : exe.verdict === 'engine_error' ? '引擎故障' : '无用例'
        : '未执行',
    },
    {
      icon: '▤',
      label: '失败簇 / 约束发现',
      value: anaIt?.analysis ? `${anaIt.analysis.failure_cluster_count} / ${anaIt.analysis.constraint_finding_count}` : '—',
      unit: anaIt?.analysis?.overall_action || '未诊断',
    },
    {
      icon: '◎',
      label: '质量门禁',
      value: g ? `${g.summary?.passed ?? '?'}/${g.summary?.total ?? '?'}` : '—',
      unit: g ? `next: ${g.next_state || '—'}` : '未评审',
    },
  ]
})

function iterFor(key: string) { return (runView.value?.iterations || []).find((v:any) => v.n === selectedRound.value && v[key]) || null }
const outcomes = computed(() => {
  const v = runView.value
  if (!v) return []
  const cards: { tone: string; title: string; text: string }[] = []
  const exeIt = iterFor('execution')
  if (exeIt?.execution) {
    const e = exeIt.execution
    cards.push({
      tone: e.verdict === 'full_pass' ? 'ok' : e.verdict === 'engine_error' ? 'bad' : 'warn',
      title:
        e.verdict === 'full_pass' ? '✓ 执行全部通过'
        : e.verdict === 'engine_error' ? '✗ 执行引擎故障'
        : `△ 执行 ${e.passed}/${e.total} 通过`,
      text:
        e.verdict === 'engine_error'
          ? `engine_error：${e.engine_error || '详见执行结果'}`
          : `execution_result.status=${e.status_raw} 仅指执行器完成，不代表用例全通过；以 ${e.passed}/${e.total} 为准。`,
    })
  }
  const regIt = iterFor('regression')
  if (regIt?.regression) {
    const r = regIt.regression
    cards.push({
      tone: r.kind === 'none' ? 'ok' : r.kind === 'real_regression' ? 'bad' : 'warn',
      title: r.kind === 'none' ? '✓ 回归检查通过' : r.kind === 'real_regression' ? '✗ 发现真实回归' : '△ 回归检查无法判定',
      text:
        r.kind === 'evaluator_unsupported'
          ? '失败原因为求值器不支持部分表达式（如 Slice），属"无法判定"而非真实回归证据。'
          : r.kind === 'mixed'
            ? '部分用例因求值器不支持无法判定，部分为真实约束违反，需人工甄别。'
            : r.kind === 'real_regression'
              ? `检出 ${r.regressions_count} 条回归，详见 regression_check.json。`
              : `检查 ${r.checked_cases} 条用例，未发现回归。`,
    })
  }
  const updIt = iterFor('constraint_update')
  if (updIt?.constraint_update) {
    cards.push({
      tone: 'info',
      title: '▤ 约束更新',
      text: `${updIt.constraint_update.change_count} 处最小修改，依据 ${(updIt.constraint_update.finding_ids || []).length} 项诊断发现。`,
    })
  } else if (iterFor('analysis')) {
    cards.push({
      tone: 'info',
      title: '▤ 未找到约束更新记录',
      text: '当前选择的轮次没有约束更新报告，暂无法确认是否修改。',
    })
  }
  return cards.slice(0, 3)
})

const SEG_KIND_TEXT: Record<string, string> = {
  normal: '常规',
  restart: '会话重启恢复',
  continuation: '授权续跑',
}

function fmtTime(at?: string | null) {
  if (!at) return '—'
  const d = new Date(at)
  return isNaN(d.getTime()) ? at : d.toLocaleString('zh-CN', { hour12: false })
}

function segStates(seg: any): string {
  const states = (seg.events || []).map((e: any) => e.state).filter(Boolean)
  return states.filter((v: string, i: number) => i === 0 || states[i - 1] !== v).join(' → ')
}
</script>

<template>
  <div ref="snapRef" class="snap-container">
    <header class="topbar">
      <div class="brand"><span class="logo"><i /><i /><i /><i /></span>算子测试工作台</div>
      <nav aria-label="页面">
        <span @click="router.push('/assets')">资产</span>
        <span class="active">运行时</span>
      </nav>
      <div class="top-right">
        <button class="text-btn" title="切换明暗主题" @click="toggle">
          {{ theme === 'dark' ? '☀ 浅色' : '☾ 深色' }}
        </button>
        <button class="text-btn" @click="historyDrawer?.open()">◷ 历史任务</button>
        <a class="text-btn" :href="'/constraints?run=' + encodeURIComponent(runId) + '&iter=iter_' + String(selectedRound || 1).padStart(3, '0')">约束审核</a><a class="text-btn" :href="'/coverage?run=' + encodeURIComponent(runId)">覆盖率</a>
      </div>
    </header>

    <aside class="round-rail" aria-label="测试轮次">
      <div class="rail-heading">测试轮次 <span>{{ rounds.length }}</span></div>
      <p class="rail-caption">选择轮次，查看过程</p>
      <nav class="round-list" aria-label="轮次选择">
        <button v-for="item in roundItems" :key="item.n" class="round-item"
          :class="{ chosen: selectedRound === item.n }"
          :aria-current="selectedRound === item.n ? 'step' : undefined"
          :aria-label="'第 ' + item.n + ' 轮，' + item.result + '，' + item.detail"
          :title="'第 ' + item.n + ' 轮 · ' + item.result + ' · ' + item.detail"
          @click="selectedRound = item.n">
          <span class="round-dot">{{ item.n }}</span>
          <span class="round-copy"><strong>第 {{ item.n }} 轮</strong>
            <small>{{ item.result }}</small><small>{{ item.detail }}</small>
            <em v-if="selectedRound === item.n">正在查看</em>
          </span>
        </button>
        <p v-if="!rounds.length" class="rail-caption">暂无轮次记录</p>
      </nav>
      <button v-if="nextRound !== null" class="latest-round" @click="selectedRound = rounds[rounds.length - 1]">查看最新轮次 →</button>
    </aside>

    <!-- ============ 第一屏：概览 + 分列详情 + 工程师带 ============ -->
    <section class="screen screen-main">
      <main class="main-area">
        <el-alert v-if="error" type="error" :title="`加载失败：${error}`" :closable="false" style="margin-bottom: 14px" />

        <template v-if="runView">
          <div class="heading">
            <div>
              <h1>{{ operatorName }}</h1>
              <div class="metadata">{{ metadata || runId }}</div>
            </div>
            <span class="status" :class="statusChip.cls">{{ statusChip.text }}</span>
          </div>

          <div class="round-heading">第 {{ selectedRound ?? '—' }} 轮汇总 <span>以下结果属于当前所选轮次</span></div>
          <div class="summary">
            <div v-for="(m, i) in metrics" :key="i" class="metric">
              <span class="icon">{{ m.icon }}</span>
              <div>
                <small>{{ m.label }}</small>
                <strong>{{ m.value }} <em>{{ m.unit }}</em></strong>
              </div>
            </div>
          </div>

          <div v-if="outcomes.length" class="outcomes">
            <div v-for="(c, i) in outcomes" :key="i" class="outcome" :class="c.tone"><strong>{{ c.title }}</strong><span>{{ c.text }}</span></div>
          </div>
          <details class="run-context"><summary>任务背景与状态历史</summary>
            <p>{{ metadata }} · {{ runId }}</p><p>文档：{{ runView.operator_doc }}</p>
            <p>提示词快照：{{ runView.current_prompt || '—' }} · 用例预算：{{ runView.case_count ?? '—' }} · 装配知识：{{ (runView.current_prompt_modules || []).length }} 项</p>
            <p>创建：{{ fmtTime(runView.created_at) }} · 最后活动：{{ fmtTime(runView.last_activity) }}</p>
            <p v-for="(seg, i) in runView.segments || []" :key="i">{{ fmtTime(seg.from_at) }} → {{ fmtTime(seg.to_at) }} · {{ segStates(seg) }}</p>
          </details>
        </template>
        <div v-else-if="!error" v-loading="true" style="height: 300px" />
      </main>

    </section>

    <!-- 汇总与头像行不参与下方流程区域的滚动。 -->
    <div class="flow-start" aria-hidden="true" />
    <div class="band-dock" >
      <div class="band-shell">
        <div class="band-grid">
          <AgentBand :engineers="roundEngineers.filter(e => e.stage)" :selected="selectedAgent" :round="selectedRound"  :scroll-left="flowScroll" :zoom="flowZoom" :viewport="flowViewport" @viewport="flowViewport = $event" @scroll-x="flowScroll = $event" @select="onAgentSelect" />
        </div>
      </div>
    </div>

    <!-- 交接图与详情共用独立的纵向滚动区域。 -->
    <section class="screen screen-board">
      <div class="board-area">
        <!-- run 级状态条：进行中/终态均有明确落点（不依赖终态事件的存在） -->
        <div class="run-status-bar" :data-terminal="runView?.is_terminal">
          <template v-if="runView?.is_terminal">
            <span class="rsb-dot rsb-terminal"></span>
            <b>任务已结束</b>
            <span>{{ statusChip.text }}</span>
          </template>
          <template v-else-if="WAITING_STATES.has(String(runView?.state || ''))">
            <span class="rsb-dot rsb-waiting"></span>
            <b>{{ statusChip.text }}</b>
            <span>用户答复后流程将继续 · 最后活动 {{ fmtTime(runView?.last_activity) }}</span>
          </template>
          <template v-else>
            <span class="rsb-dot rsb-running"></span>
            <b>任务进行中</b>
            <span>当前阶段 {{ runView?.state || '…' }} · 最后活动 {{ fmtTime(runView?.last_activity) }}</span>
          </template>
        </div>
        <HandoffBoard
          ref="flowBoard"
          :scroll-left="flowScroll"
          :zoom="flowZoom"
          :viewport="flowViewport"
          @scroll-x="flowScroll = $event"
          @update:zoom="flowZoom = $event"
          @select-agent="selectedAgent = $event"
          v-if="runView"
          :run-id="runId"
          :run-view="runView"
          :engineers="roundEngineers"
          :events="replayEvents || []"
          :round="selectedRound ?? 1"
        />

        <div class="round-pager" aria-label="切换轮次">
          <button :disabled="previousRound === null" @click="previousRound !== null && (selectedRound = previousRound)">← 上一轮</button>
          <span aria-live="polite">正在查看第 {{ selectedRound }} 轮 · 共 {{ rounds.length }} 轮</span>
          <button :disabled="nextRound === null" @click="nextRound !== null && (selectedRound = nextRound)">下一轮 →</button>
        </div>
        <footer class="page-foot">
          <span>数据来自 {{ runId }} 的落盘产物</span>
          <span>状态与交接由产物推导（页面以「推导」标记）· 时间来自 history 与文件修改时间 · 最后活动 {{ fmtTime(runView?.last_activity) }}</span>
        </footer>
      </div>
    </section>

    <HistoryDrawer ref="historyDrawer" :current-run-id="runId" />
  </div>
</template>

<style scoped>
.snap-container { position: relative; height: 100vh; height: 100dvh; overflow-y: auto; scroll-snap-type: none; }
.screen {
  padding-bottom: 0; min-height: 100vh; min-height: 100dvh; scroll-snap-align: start; display: flex; flex-direction: column; }

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  height: 72px;
  background: var(--wb-card);
  border-bottom: 1px solid var(--wb-line);
  display: flex;
  align-items: center;
  padding: 0 36px;
  gap: 42px;
}
.brand { font-size: 20px; font-weight: 750; letter-spacing: 0.5px; display: flex; align-items: center; }
.logo { display: inline-grid; grid-template-columns: repeat(2, 9px); gap: 3px; margin-right: 12px; }
.logo i { width: 9px; height: 9px; border-radius: 2px; background: var(--wb-blue); }
.topbar nav { height: 100%; display: flex; align-items: center; gap: 30px; }
.topbar nav span { height: 100%; padding-top: 24px; color: var(--wb-muted); cursor: pointer; }
.topbar nav .active { color: var(--wb-blue); border-bottom: 3px solid var(--wb-blue); font-weight: 650; }
.top-right { margin-left: auto; display: flex; gap: 16px; align-items: center; }
.tag { font-size: 12px; border-radius: 6px; padding: 4px 9px; background: var(--wb-blue-soft); color: var(--wb-blue); }
.text-btn { border: 0; background: none; color: var(--wb-muted); font-size: 12px; }
.text-btn:hover { color: var(--wb-blue); }

.main-area { flex: 1; padding: 22px 36px 12px; max-width: 1800px; width: 100%; margin: 0 auto; overflow-y: auto; min-height: 0; }
.heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
h1 { font-size: clamp(20px, 2.2vw, 28px); margin: 0 0 4px; letter-spacing: -0.8px; word-break: break-all; }
.metadata { color: var(--wb-muted); margin-top: 6px; font-size: 12.5px; }
.status { padding: 6px 13px; border-radius: 20px; font-weight: 600; font-size: 13px; white-space: nowrap; }
.status.ok { color: var(--wb-green); background: var(--wb-green-soft); }
.status.warn { color: var(--wb-orange); background: var(--wb-orange-soft); }
.status.bad { color: var(--wb-red); background: var(--wb-red-soft); }
.status.run { color: var(--wb-blue); background: var(--wb-blue-soft); }

.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; margin: 18px 0; }
.metric {
  background: var(--wb-card);
  border: 1px solid var(--wb-line);
  border-radius: 12px;
  padding: 12px 18px;
  display: flex;
  gap: 14px;
  align-items: center;
}
.metric .icon {
  font-size: 20px;
  background: var(--wb-blue-soft);
  color: var(--wb-blue);
  border-radius: 12px;
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
}
.metric small { display: block; color: var(--wb-muted); font-size: 11.5px; }
.metric strong { font-size: 21px; letter-spacing: -0.5px; }
.metric em { font-style: normal; font-size: 12px; font-weight: 400; color: var(--wb-muted); margin-left: 6px; }

.detail-area { min-height: 0; }
.overview { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
.ov-card {
  background: var(--wb-card);
  border: 1px solid var(--wb-line);
  border-radius: 12px;
  padding: 14px 16px;
  max-height: 46vh;
  max-height: 46dvh;
  overflow: auto;
}
.ov-title { font-weight: 700; color: var(--wb-ink); margin-bottom: 10px; }
.ov-run-id { color: var(--wb-faint); font-size: 11px; word-break: break-all; margin-bottom: 8px; }
.ov-kv { margin: 0; }
.ov-kv dt { color: var(--wb-muted); font-size: 11px; margin-top: 8px; }
.ov-kv dd { margin: 2px 0 0; color: var(--wb-ink); font-size: 12.5px; word-break: break-all; }
.ov-hint { margin-top: 16px; color: var(--wb-blue); font-size: 12px; }
.segments { padding-left: 2px; margin-top: 4px; }
.seg-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.seg-kind { font-size: 11px; padding: 1px 8px; border-radius: 6px; background: var(--wb-blue-soft); color: var(--wb-blue); }
.seg-states { font-size: 12px; color: var(--wb-muted); }

.band-wrap { position: relative; }
.expand-btn {
  position: absolute;
  top: -32px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--wb-card);
  color: var(--wb-muted);
  border: 1px solid var(--wb-line);
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  padding: 5px 20px;
  font-size: 12px;
  cursor: pointer;
  z-index: 5;
  white-space: nowrap;
}
.expand-btn:hover { color: var(--wb-blue); }

.screen-board { background: var(--wb-bg); }
.board-area { padding: 0 36px 30px; max-width: 1800px; width: 100%; margin: 0 auto; }
.section-title { display: flex; align-items: center; gap: 16px; margin: 4px 0 12px; }
.section-title h2 { font-size: 20px; margin: 0; }
.section-title > span { color: var(--wb-muted); font-size: 12px; }
.collapse-btn {
  margin-left: auto;
  border: 1px solid var(--wb-line);
  background: var(--wb-card);
  color: var(--wb-muted);
  border-radius: 8px;
  padding: 5px 16px;
  font-size: 12px;
}
.collapse-btn:hover { color: var(--wb-blue); }

.outcomes { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin-top: 20px; }
.outcome { background: var(--wb-card); border: 1px solid var(--wb-line); border-radius: 10px; padding: 15px 18px; }
.outcome strong { display: block; font-size: 13px; margin-bottom: 5px; }
.outcome span { font-size: 12px; color: var(--wb-muted); line-height: 1.6; }
.outcome.ok strong { color: var(--wb-green); }
.outcome.warn strong { color: var(--wb-orange); }
.outcome.bad strong { color: var(--wb-red); }
.outcome.info strong { color: var(--wb-blue); }

.page-foot {
  margin: 22px 0;
  color: var(--wb-muted);
  font-size: 11px;
  display: flex;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}

@media (max-width: 900px) {
  .main-area, .board-area { padding-left: 16px; padding-right: 16px; }
  .topbar { padding: 0 16px; gap: 20px; }
  .brand { font-size: 15px; }
  .top-right .tag { display: none; }
  .topbar nav { gap: 14px; }
}
@media (max-width: 600px) {
  .top-right { gap: 10px; }
  .heading { flex-direction: column; align-items: flex-start; gap: 8px; }
  .status { align-self: flex-start; }
  .metric { padding: 10px 14px; }
  .metric strong { font-size: 18px; }
  .expand-btn { padding: 5px 12px; font-size: 11px; }
  .page-foot { flex-direction: column; gap: 6px; }
}
.flow-start { height: 0; }
.band-dock { position: sticky; top: 72px; z-index: 19; background: var(--wb-card); border-bottom: 1px solid var(--wb-line); box-shadow: 0 4px 12px #152b4909; }
.band-shell { max-width: 1800px; margin: auto; padding: 0 36px; }
.band-grid { display: grid; grid-template-columns: minmax(0, 1fr) clamp(260px,24vw,330px); gap:18px; }
.band-grid > :first-child { min-width:0; margin:0 1px; }
.band-actions { display:flex;align-items:center;justify-content:center; }
.screen-main { min-height:calc(100dvh - 72px - 150px); }
.screen-board { min-height:calc(100dvh - 222px); }
@media(max-width:900px){.band-shell{padding:0 16px}.band-grid{grid-template-columns:minmax(0,1fr);gap:0}.band-actions{position:absolute;right:20px;top:-32px}.band-actions button{font-size:11px;padding:4px 10px}}

.board-area { transition: transform .1s ease-out; }
.run-status-bar { display:flex; align-items:center; gap:10px; padding:8px 16px; margin:12px 16px 0; border:1px solid var(--wb-line); border-radius:8px; background:var(--wb-card); font-size:12.5px; color:var(--wb-muted); }
.run-status-bar b { color: var(--wb-ink); }
.rsb-dot { width:9px; height:9px; border-radius:50%; }
.rsb-running { background: var(--wb-orange); animation: rsbPulse 1.6s ease-in-out infinite; }
.rsb-terminal { background: var(--wb-green); }
.rsb-waiting { background: var(--wb-yellow, #b0a06a); animation: rsbPulse 2.4s ease-in-out infinite; }
.run-status-bar[data-terminal="false"] b { color: var(--wb-orange); }
@keyframes rsbPulse { 0%,100%{opacity:1} 50%{opacity:.35} }
.screen-board { scroll-margin-top: 72px; }
@media(prefers-reduced-motion: reduce){.board-area{transition:none}}
/* Full-width shared table: no reserved detail column or centered max-width. */
.band-shell { max-width:none; padding:0; width:100%; }
.band-grid { display:block; position:relative; }
.band-grid > :first-child { margin:0; width:100%; }
.band-actions { position:absolute; right:8px; top:-31px; }
.band-actions button { padding:4px 10px; font-size:11px; }
.board-area { max-width:none; padding:0 0 24px; width:100%; }
.outcomes,.page-foot { margin-left:16px; margin-right:16px; }

/* 轮次导航独立于横向泳道，滚动不会改变当前轮次。 */
.round-rail { position:fixed; top:72px; bottom:0; left:0; width:174px; z-index:18; background:var(--wb-card); border-right:1px solid var(--wb-line); padding:24px 10px; overflow-y:auto; }
.rail-heading { padding:0 10px; font-size:13px; font-weight:700; display:flex; justify-content:space-between; color:var(--wb-ink); }
.rail-heading span { color:var(--wb-muted); font-weight:400; }
.rail-caption { padding:0 10px; font-size:11px; color:var(--wb-muted); margin:8px 0 22px; }
.round-list { display:flex; flex-direction:column; align-items:stretch; gap:0; height:auto; }
.round-item { position:relative; display:flex; gap:10px; width:100%; min-height:112px; padding:14px 10px; border:0; border-radius:9px; background:transparent; color:var(--wb-ink); text-align:left; cursor:pointer; }
.round-item:not(:last-child)::after { content:''; position:absolute; left:23px; top:43px; bottom:-14px; width:1px; background:var(--wb-line); }
.round-item:hover { background:var(--wb-hover); }
.round-item.chosen { background:var(--wb-blue-soft); }
.round-item.chosen::before { content:''; position:absolute; left:-10px; top:18px; width:3px; height:28px; background:var(--wb-blue); border-radius:0 3px 3px 0; }
.round-dot { display:grid; place-items:center; flex:0 0 27px; height:27px; border:1px solid var(--wb-line); border-radius:50%; color:var(--wb-muted); background:var(--wb-card); font-size:12px; z-index:1; }
.chosen .round-dot { color:var(--wb-blue); border-color:var(--wb-blue); }
.round-copy { display:flex; flex-direction:column; gap:5px; padding-top:5px; }
.round-copy strong { font-size:12px; }.round-copy small { font-size:10px; color:var(--wb-muted); line-height:1.5; }.round-copy em { font-size:10px; font-style:normal; color:var(--wb-blue); }
.latest-round { margin:20px 0 0; border:0; background:transparent; color:var(--wb-blue); font-size:11px; cursor:pointer; }
.round-pager { display:flex; justify-content:space-between; align-items:center; gap:12px; margin:24px 16px; color:var(--wb-muted); font-size:12px; }
.round-pager button { border:1px solid var(--wb-line); border-radius:8px; padding:9px 16px; background:var(--wb-card); color:var(--wb-blue); cursor:pointer; }.round-pager button:disabled { opacity:.4; cursor:default; }
.round-item:focus-visible,.latest-round:focus-visible,.round-pager button:focus-visible { outline:2px solid var(--wb-blue); outline-offset:2px; }
.screen-main,.band-dock,.screen-board { margin-left:174px; }
@media(max-width:900px){
  .round-rail{width:64px;padding:20px 6px}.rail-heading{font-size:11px;padding:0;justify-content:center}.rail-heading span,.rail-caption,.round-copy,.latest-round{display:none}
  .round-list{margin-top:20px}.round-item{min-height:64px;padding:12px;justify-content:center}.round-item:not(:last-child)::after{left:25px;top:40px;bottom:-12px}.round-item.chosen::before{left:-6px}
  .screen-main,.band-dock,.screen-board{margin-left:64px}.round-pager{gap:6px}.round-pager button{padding:8px}.round-pager span{font-size:10px}
}

.view-switcher { position:sticky; top:-24px; z-index:3; display:flex; flex-direction:column; gap:5px; padding:0 0 18px; margin-bottom:22px; border-bottom:1px solid var(--wb-line); background:var(--wb-card); }
.view-heading { padding:0 10px 8px; font-size:11px; color:var(--wb-muted); }
.view-switcher button { display:flex; align-items:center; gap:10px; padding:10px; border:1px solid transparent; border-radius:8px; background:transparent; color:var(--wb-muted); font:inherit; font-size:12px; cursor:pointer; text-align:left; }
.view-switcher button:hover { background:var(--wb-hover); }
.view-switcher button.selected { color:var(--wb-blue); border-color:var(--wb-blue-line); background:var(--wb-blue-soft); font-weight:600; }
.view-switcher button:focus-visible { outline:2px solid var(--wb-blue); outline-offset:2px; }
.view-switcher svg { width:18px; height:18px; flex-shrink:0; fill:none; stroke:currentColor; stroke-width:1.6; stroke-linecap:round; stroke-linejoin:round; }
@media(max-width:900px){.view-switcher{top:-20px;padding-bottom:14px;margin-bottom:18px}.view-heading,.view-switcher button span{display:none}.view-switcher button{justify-content:center;padding:10px 0}}

.screen-main,.screen-board { min-height:0; }
.main-area { max-width:none; padding:20px 24px 16px; overflow:visible; }
.summary { margin:12px 0; }
.main-area .outcomes { margin:10px 0 0; gap:10px; }
.main-area .outcome { padding:10px 14px; }
.round-heading { font-size:13px; font-weight:650; color:var(--wb-ink); }
.round-heading span { margin-left:10px; font-size:11px; font-weight:400; color:var(--wb-muted); }
.run-context { margin-top:12px; font-size:11px; color:var(--wb-muted); overflow-wrap:anywhere; }
.run-context summary { cursor:pointer; }.run-context p { margin:8px 0; }

/* 固定顶部区域，用剩余高度承载流程与详情，避免依赖固定汇总高度。 */
.snap-container { display:flex; flex-direction:column; overflow:hidden; }
.topbar { position:relative; top:auto; flex:0 0 72px; }
.screen-main { flex:0 0 auto; max-height:36dvh; min-height:0; overflow:auto; overscroll-behavior:contain; }
.main-area { flex:0 0 auto; }
.band-dock { position:relative; top:auto; flex:0 0 auto; }
.screen-board { flex:1 1 0; min-height:0; overflow:auto; overscroll-behavior:contain; scroll-margin-top:0; }
.board-area { flex:0 0 auto; }
.screen-board :deep(.scroller) { max-height:none; }
.screen-board :deep(.unified-detail) { scroll-margin-top:12px; }
</style>
