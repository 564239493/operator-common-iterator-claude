<script setup lang="ts">
import { computed, ref } from 'vue'
import { stageInputs } from './stage-inputs'
import ArtifactViewer from './ArtifactViewer.vue'
import LogTailBox from './LogTailBox.vue'
import { ARTIFACT_MAP } from '../board/artifact-map'
import { agentColor } from '../../composables/useEngineers'
import { STATUS_TEXT, STATUS_COLOR } from '../../api/types'

/** 第一屏：点击机器人后上方的 N 列详情（输入 / 技能知识 / 产物 / 能否进入下一阶段及依据） */
const props = defineProps<{
  runId: string
  runView: any
  round: number
  engineer: any // AgentDef + runtime 合并对象
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const viewer = ref<InstanceType<typeof ArtifactViewer>>()

const iterations = computed<any[]>(() => props.runView?.iterations || [])
const currentIter = computed(() => iterations.value.find((v: any) => v.n === props.round) || null)

const artifactEntries = computed(() => {
  const name = props.engineer?.name
  const n = currentIter.value?.n
  if (!name || !n) return []
  return (ARTIFACT_MAP[name] || []).map((a) => ({ ...a, fullPath: a.path(n) }))
})

function openArtifact(path: string) {
  viewer.value?.open(path)
}

// 本阶段摘要行（按 agent 类型取 iteration 视图中的现成摘要）
const summaryLines = computed(() => {
  const it = currentIter.value
  if (!it) return []
  const name = props.engineer?.name
  const lines: { label: string; value: string; cls?: string }[] = []
  const push = (label: string, value: any, cls?: string) => {
    if (value !== null && value !== undefined && value !== '') lines.push({ label, value: String(value), cls })
  }
  if (name === 'constraint-checker' && it.constraint_check) {
    const c = it.constraint_check
    push('检查结论', c.status, c.status === 'passed' ? 'ok' : 'bad')
    push('轮次', `${c.current_round}/${c.max_rounds}`)
    push('问题 open/fixed/unfixed', `${c.issues_open}/${c.issues_fixed}/${c.issues_unfixed}`)
  }
  if (name === 'case-generator' && it.generation) {
    const p = it.generation.progress || {}
    push('进程状态', p.state, p.state === 'complete' ? 'ok' : undefined)
    push('pid 存活', p.pid_alive ? '是' : '否')
    push('生成总数', p.total ?? it.generation.summary?.total)
    push('耗时（秒）', p.elapsed_seconds?.toFixed?.(1))
  }
  if (name === 'case-executor' && it.execution) {
    const e = it.execution
    push('通过/总数', `${e.passed}/${e.total}`, e.failed > 0 ? 'warn' : 'ok')
    push('verdict', e.verdict)
    push('engine_error', e.engine_error || '无', e.engine_error ? 'bad' : undefined)
    push('plog 错误数', e.plog_error_count)
  }
  if (name === 'failure-analyst' && it.analysis) {
    const a = it.analysis
    push('overall_action', a.overall_action, 'warn')
    push('失败簇 / 约束发现', `${a.failure_cluster_count} / ${a.constraint_finding_count}`)
    push('root_cause', a.root_cause)
  }
  if (name === 'constraint-updater') {
    if (it.constraint_update) {
      push('修改处数', it.constraint_update.change_count)
      push('status', it.constraint_update.status)
    } else {
      push('约束更新', '本轮无 constraint_update.json（约束未变，可能仅扩量）')
    }
    if (it.regression) {
      const r = it.regression
      push(
        '回归检查',
        r.kind === 'evaluator_unsupported' ? '求值器不支持，非真实回归' : r.kind,
        r.kind === 'real_regression' ? 'bad' : r.kind === 'none' ? 'ok' : 'warn',
      )
    }
  }
  if (name === 'quality-reviewer' && it.quality_gate) {
    const g = it.quality_gate
    push('gate status', g.status_raw || g.status, g.status === 'pass' ? 'ok' : 'bad')
    push('blocking_issues', g.blocking_issues?.length || 0, g.blocking_issues?.length ? 'bad' : undefined)
  }
  return lines
})

const gate = computed(() => currentIter.value?.quality_gate || null)

const gateOk = computed(() => {
  if (!gate.value) return false
  return gate.value.status === 'pass' && !(gate.value.blocking_issues || []).length
})

const inputMaterials = computed(() => stageInputs(props.engineer?.name, props.round, props.runView))
const scene = computed(() => props.runView?.scene || {})
const runtime = computed(() => props.engineer?.runtime || null)
</script>

<template>
  <div class="stage-detail">
    <div class="detail-head">
      <div class="who">
        <span class="dot" :style="{ background: agentColor(engineer.color) }" />
        <b>{{ engineer.name }}</b>
        <span class="role">{{ engineer.role }}</span>
        <span
          v-if="runtime"
          class="status-pill"
          :style="{ background: (STATUS_COLOR[runtime.status] || '#aab2be') + '22', color: STATUS_COLOR[runtime.status] }"
        >
          {{ STATUS_TEXT[runtime.status] || runtime.status }}
        </span>
        <el-tooltip v-if="runtime?.basis" :content="runtime.basis" placement="bottom">
          <span class="basis-tag">推导依据</span>
        </el-tooltip>
      </div>
      <div class="head-right">
        <button class="close-btn" @click="emit('close')">收起 ✕</button>
      </div>
    </div>

    <div class="cols">
      <!-- 列 1：输入 -->
      <div class="col-card">
        <div class="col-title">本阶段参考材料</div>
        <p class="input-note">按角色流程定义列出的主要材料，不代表本次实际读取记录。</p>
        <dl class="kv">
          <template v-for="item in inputMaterials" :key="item.path">
            <dt>{{ item.label }}</dt><dd>{{ item.path }}<small class="input-evidence">{{ item.evidence }}</small></dd>
          </template>
        </dl>
        <div v-if="!inputMaterials.length" class="empty">暂无可核对的输入定义</div>
        <details class="task-context"><summary>任务配置（全流程共用）</summary>

        <dl class="kv">
          <dt>算子文档</dt>
          <dd>{{ runView?.operator_doc || '—' }}</dd>
          <dt>测试框架 / 模式</dt>
          <dd>{{ runView?.test_framework }} / {{ runView?.mode }}</dd>
          <dt>场景</dt>
          <dd v-if="scene.enabled">
            {{ (scene.device_types || []).join('、') }}
            <template v-if="scene.selection"> · {{ Object.keys(scene.selection).length }} 设备已选</template>
          </dd>
          <dd v-else>未启用</dd>
          <dt>提示词快照</dt>
          <dd>{{ runView?.current_prompt || '—' }}</dd>
          <dt>用例预算</dt>
          <dd>{{ runView?.case_count ?? '—' }}</dd>
        </dl>

        </details>
        <div v-if="summaryLines.length" class="mini-summary">
          <div class="col-sub">本阶段摘要</div>
          <div v-for="(l, i) in summaryLines" :key="i" class="sum-line">
            <span class="sum-label">{{ l.label }}</span>
            <span class="sum-value" :class="l.cls">{{ l.value }}</span>
          </div>
        </div>
      </div>

      <!-- 列 2：技能与知识 -->
      <div class="col-card">
        <div class="col-title">技能 / 知识库</div>
        <div class="col-sub">预加载 Skill</div>
        <div class="tags">
          <span v-for="s in engineer.skills || []" :key="s" class="skill-chip">{{ s }}</span>
          <span v-if="!(engineer.skills || []).length" class="empty">无</span>
        </div>
        <div class="col-sub">装配冻结的知识模块（{{ (runView?.current_prompt_modules || []).length }}）</div>
        <div class="tags">
          <span v-for="m in runView?.current_prompt_modules || []" :key="m" class="skill-chip faint">{{ m }}</span>
        </div>
        <div class="col-sub">可用工具</div>
        <div class="tools">{{ engineer.tools || '—' }}</div>
      </div>

      <!-- 列 3：产物（实时刷新） -->
      <div class="col-card">
        <div class="col-title">产物</div>
        <div v-if="!artifactEntries.length" class="empty">该角色无关联产物</div>
        <div v-for="a in artifactEntries" :key="a.fullPath" class="artifact-line">
          <span class="artifact-name" :title="a.fullPath">{{ a.label }}</span>
          <button class="view-btn" @click="openArtifact(a.fullPath)">查看 JSON</button>
        </div>
        <LogTailBox
          v-if="engineer.name === 'case-generator' && currentIter"
          :run-id="runId"
          :iteration="currentIter.n"
          name="generation_console.log"
          title="生成进程日志"
        />
        <LogTailBox
          v-if="engineer.name === 'case-executor' && currentIter"
          :run-id="runId"
          :iteration="currentIter.n"
          name="execution.log"
          title="执行日志"
        />
      </div>

      <!-- 列 4：门禁与依据 -->
      <div class="col-card">
        <div class="col-title">能否进入下一阶段</div>
        <template v-if="gate">
          <div class="gate-box" :class="{ ok: gateOk }">
            {{ gateOk ? `允许：gate ${gate.status_raw || gate.status} → ${gate.next_state || '—'}`
                       : `不允许：${(gate.blocking_issues || []).length ? `${gate.blocking_issues.length} 条阻断` : `gate ${gate.status_raw || gate.status}`} → ${gate.next_state || '—'}` }}
          </div>
          <div v-if="gate.summary" class="gate-summary">
            检查 {{ gate.summary.passed ?? '?' }}/{{ gate.summary.total }} 通过
            <span v-if="gate.summary_derived" class="mini-badge" title="顶层无汇总键，由 checks 现算">现算</span>
          </div>
          <div v-if="(gate.blocking_issues || []).length" class="blocking">
            <div class="col-sub" style="color: var(--wb-red)">阻断项</div>
            <div v-for="(b, i) in gate.blocking_issues" :key="i" class="blocking-item">{{ b }}</div>
          </div>
          <div class="checks">
            <div v-for="(c, i) in gate.checks || []" :key="i" class="check-line">
              <span class="check-tag" :class="c.result">{{ c.result }}</span>
              <el-tooltip :content="c.detail || '（无明细）'" placement="left">
                <span class="check-name">{{ c.name }}</span>
              </el-tooltip>
            </div>
          </div>
        </template>
        <div v-else class="empty">本轮尚无 quality_gate.json</div>
        <div class="col-sub">判定依据（后端原文透出，前端不做二次裁决）</div>
        <div class="basis">{{ runtime?.basis || '—' }}</div>
      </div>
    </div>

    <ArtifactViewer ref="viewer" :run-id="runId" />
  </div>
</template>

<style scoped>
.stage-detail {
  background: var(--wb-card);
  border: 1px solid var(--wb-line);
  border-radius: 14px;
  padding: 12px 14px;
}
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px; }
.who { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.who .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.who .role { color: var(--wb-muted); font-size: 12px; }
.status-pill { font-size: 11px; padding: 2px 9px; border-radius: 12px; font-weight: 600; }
.basis-tag {
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 6px;
  background: var(--wb-line);
  color: var(--wb-muted);
  cursor: default;
}
.head-right { display: flex; align-items: center; gap: 8px; }
.close-btn { border: 0; background: none; color: var(--wb-muted); font-size: 12px; }
.close-btn:hover { color: var(--wb-blue); }

.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; }
.col-card {
  background: var(--wb-card-soft);
  border: 1px solid var(--wb-line);
  border-radius: 10px;
  padding: 10px 12px;
  min-height: 240px;
  max-height: 380px;
  max-height: min(380px, 52dvh);
  overflow: auto;
  font-size: 12.5px;
}
.col-title { font-weight: 700; color: var(--wb-ink); margin-bottom: 8px; font-size: 13px; }
.col-sub { color: var(--wb-muted); font-size: 11.5px; margin: 10px 0 5px; }
.kv { margin: 0; }
.kv dt { color: var(--wb-muted); font-size: 11px; margin-top: 7px; }
.kv dd { margin: 1px 0 0; color: var(--wb-ink); word-break: break-all; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.skill-chip {
  display: inline-block;
  font: 10px/1.5 ui-monospace, monospace;
  padding: 4px 7px;
  background: var(--wb-blue-soft);
  border: 1px solid var(--wb-blue-line);
  border-radius: 5px;
  color: var(--wb-ink);
  overflow-wrap: anywhere;
}
.skill-chip.faint { background: var(--wb-code-bg); border-color: var(--wb-line); }
.tools { color: var(--wb-muted); font-size: 11.5px; }
.empty { color: var(--wb-faint); font-size: 12px; }
.mini-summary { margin-top: 8px; border-top: 1px dashed var(--wb-line); padding-top: 4px; }
.sum-line { display: flex; justify-content: space-between; align-items: center; margin: 4px 0; gap: 8px; }
.sum-label { color: var(--wb-muted); font-size: 11.5px; flex: 0 0 auto; }
.sum-value.ok { color: var(--wb-green); font-weight: 600; }
.sum-value.warn { color: var(--wb-orange); font-weight: 600; }
.sum-value.bad { color: var(--wb-red); font-weight: 600; }
.artifact-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;
  border-bottom: 1px dashed var(--wb-line-soft);
}
.artifact-name { color: var(--wb-ink); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.view-btn {
  flex: 0 0 auto;
  border: 0;
  border-radius: 5px;
  background: var(--wb-blue-soft);
  color: var(--wb-blue);
  padding: 3px 9px;
  font-size: 10.5px;
}
.gate-box {
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12px;
  background: var(--wb-red-soft);
  color: var(--wb-red);
  border: 1px solid var(--wb-red-line);
}
.gate-box.ok { background: var(--wb-green-soft); color: var(--wb-green); border-color: var(--wb-green-line); }
.gate-summary { margin: 8px 0; color: var(--wb-muted); display: flex; gap: 6px; align-items: center; font-size: 12px; }
.mini-badge { font-size: 9.5px; padding: 0 5px; border-radius: 6px; background: var(--wb-orange-soft); color: var(--wb-orange); }
.blocking-item { color: var(--wb-red); font-size: 12px; padding: 2px 0; }
.checks { margin-top: 8px; max-height: 160px; overflow: auto; }
.check-line { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
.check-tag { font-size: 10px; padding: 1px 7px; border-radius: 5px; font-weight: 600; }
.check-tag.pass { background: var(--wb-green-soft); color: var(--wb-green); }
.check-tag.fail { background: var(--wb-red-soft); color: var(--wb-red); }
.check-tag.warn { background: var(--wb-orange-soft); color: var(--wb-orange); }
.check-tag.unknown { background: var(--wb-line); color: var(--wb-muted); }
.check-name { color: var(--wb-muted); font-size: 11.5px; cursor: default; }
.basis { color: var(--wb-muted); font-size: 11.5px; line-height: 1.6; }

@media (max-width: 600px) {
  .col-card { min-height: 0; }
}
.input-note,.input-evidence{font-size:11px;color:var(--wb-muted);line-height:1.6}.input-evidence{display:block;margin-top:3px}.task-context{margin-top:18px;border-top:1px solid var(--wb-line);padding-top:10px}.task-context summary{cursor:pointer;font-size:12px;color:var(--wb-blue)}
</style>
