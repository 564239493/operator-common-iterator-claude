<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { stageInputs } from '../stage/stage-inputs'
import RobotHead from '../band/RobotHead.vue'
import ArtifactViewer from '../stage/ArtifactViewer.vue'
import LogTailBox from '../stage/LogTailBox.vue'
import { ARTIFACT_MAP } from './artifact-map'
import { agentColor } from '../../composables/useEngineers'
import { api } from '../../api/client'

/**
 * 节点详情栏（右侧 sticky，demo.html 的 aside 范式 + 本项目的四方面内容）：
 * 关键产出 / 引用技能 / 知识模块 / 产物证据（可查看原始 JSON）/ 能否进入下一阶段及依据。
 */
const props = defineProps<{
  runId: string
  runView: any
  node: any
  edge: any
  engineer: any
  kindText: string
}>()

const viewer = ref<InstanceType<typeof ArtifactViewer>>()

const round = computed(() => props.node?.round || 1)

const iterationView = computed(() =>
  (props.runView?.iterations || []).find((v: any) => v.n === round.value) || null,
)

const summaryLines = computed(() => {
  const it = iterationView.value
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
    push('问题 未解决/已修复/仍未修复', `${c.issues_open}/${c.issues_fixed}/${c.issues_unfixed}`)
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
      push('约束更新', '本轮未保留约束更新报告，无法确认是否修改')
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

const inputMaterials = computed(() => stageInputs(props.engineer?.name, round.value, props.runView))

// 附加展示字段：优先取节点事件（edge），回退 run 级 runtime（supplementer/optimizer 的
// application / origins / decisions 随共享判据附带）
const extras = computed(() => {
  const source = props.edge?.decisions !== undefined || props.edge?.application !== undefined
    ? props.edge
    : props.engineer?.runtime || {}
  return {
    application: source.application ?? null,
    origins: source.origins || null,
    decisions: source.decisions ?? null,
  }
})

const gate = computed(() => iterationView.value?.quality_gate || null)

const artifacts = computed(() => {
  if (!props.engineer?.name) return []
  return (ARTIFACT_MAP[props.engineer.name] || []).map((a) => ({
    ...a,
    fullPath: a.path(round.value),
  }))
})

// extractor 节点：按需取 extraction_provenance（知识应用记录）
const provenance = ref<any[] | null>(null)
watch(
  () => [props.runId, props.node?.id],
  async () => {
    provenance.value = null
    if (props.engineer?.name !== 'constraint-extractor') return
    try {
      const data = await api.artifact(props.runId, 'iter_001/extraction_provenance.json')
      if (data?.json && Array.isArray(data.json.modules_applied)) {
        provenance.value = data.json.modules_applied
      }
    } catch {
      provenance.value = null
    }
  },
  { immediate: true },
)

const gateVerdict = computed(() => {
  if (!gate.value) return null
  const g = gate.value
  const blocking = (g.blocking_issues || []).length
  const ok = g.status === 'pass' && !blocking
  return {
    ok,
    text: ok
      ? `允许：gate ${g.status_raw || g.status} → ${g.next_state || '—'}`
      : `不允许：${blocking ? `${blocking} 条阻断` : `gate ${g.status_raw || g.status}`} → ${g.next_state || '—'}`,
  }
})

function openArtifact(path: string) {
  viewer.value?.open(path)
}
</script>

<template>
  <aside class="detail" aria-live="polite">
    <template v-if="node && engineer">
      <h3>处理详情</h3>
      <div class="node-heading">
        <RobotHead
          :color="agentColor(engineer.color)"
          :status="engineer.runtime?.status || 'pending'"
          :agent="engineer.name"
          :size="52"
        />
        <div>
          <b>{{ engineer.role || engineer.name }}</b>
          <small>{{ engineer.name }}</small>
        </div>
      </div>

      <div class="detail-badge">{{ kindText }}</div>

      <div class="detail-label">交接</div>
      <p class="subtle">第 {{ round }} 轮 · {{ edge?.from || '—' }} → {{ edge?.to || '无下游记录' }} · {{ edge?.time || '时间未记录' }}</p>
      <div class="key-output">{{ edge?.basis || '（无依据记录）' }}</div>

      <div class="detail-label">本轮保留的阶段结果</div>
      <dl class="merged-kv"><template v-for="line in summaryLines" :key="line.label"><dt>{{ line.label }}</dt><dd>{{ line.value }}</dd></template></dl>
      <p v-if="!summaryLines.length" class="subtle">暂无结构化阶段摘要，可查看下方产物。</p>
      <div class="detail-label">本阶段参考材料</div>
      <dl class="merged-kv"><template v-for="item in inputMaterials" :key="item.path"><dt>{{ item.label }}</dt><dd>{{ item.path }}<small>{{ item.evidence }}</small></dd></template></dl>
      <div class="detail-label">引用技能</div>
      <div>
        <span v-for="s in engineer.skills || []" :key="s" class="skill-chip">{{ s }}</span>
        <span v-if="!(engineer.skills || []).length" class="subtle">未配置预加载技能</span>
      </div>

      <!-- 定义加载状态：不伪装成功 -->
      <div v-if="engineer.definition_found === false" class="def-notice">
        该角色为流程固定角色，未找到定义文件。
      </div>
      <div v-else-if="engineer.load_error" class="def-notice def-notice-bad">
        定义文件解析失败：{{ engineer.load_error }}
      </div>

      <!-- 补充约束：补充结果与应用情况分开（合并归主协调器） -->
      <template v-if="engineer.name === 'constraint-supplementer' && (extras.application || extras.origins)">
        <div class="detail-label">补丁应用情况</div>
        <p class="subtle">{{ extras.application || '—（空补丁无需应用；origin 计数仅辅助信息）' }}</p>
      </template>

      <!-- 提示词优化：裁决与提案互不推翻 -->
      <template v-if="engineer.name === 'prompt-optimizer' && extras.decisions">
        <div class="detail-label">裁决记录</div>
        <p v-if="extras.decisions.status === 'broken'" class="def-notice def-notice-bad">
          裁决文件读取异常：{{ extras.decisions.error || '解析失败' }}（不推翻提案已生成的事实）
        </p>
        <p v-else class="subtle">
          {{ extras.decisions.status === 'ok'
            ? `已记录 ${extras.decisions.count ?? 0} 条裁决`
            : '未找到裁决文件' }}
        </p>
      </template>

      <details v-if="(runView?.current_prompt_modules || []).length" class="skill-record">
        <summary>参考知识 · {{ runView.current_prompt_modules.length }} 项</summary>
        <div class="modules">
          <span v-for="m in runView.current_prompt_modules" :key="m" class="skill-chip">{{ m }}</span>
        </div>
      </details>
      <details v-if="provenance?.length" class="skill-record">
        <summary>本轮知识应用记录 · {{ provenance.length }} 项</summary>
        <ul>
          <li v-for="(k, i) in provenance" :key="i">
            <b>{{ k.skill || k.module_id }}</b><br />
            {{ k.status === 'applied' ? '已应用' : k.status }} · {{ k.reason }}
          </li>
        </ul>
      </details>

      <div class="detail-label">产物 / 证据来源</div>
      <div v-if="artifacts.length" class="files">
        <div v-for="a in artifacts" :key="a.fullPath" class="file-line">
          <span class="file-path">{{ a.fullPath }}</span>
          <button class="view-btn" @click="openArtifact(a.fullPath)">查看 JSON</button>
        </div>
      </div>
      <div v-else class="subtle">该角色无关联产物</div>

      <template v-if="gate && engineer.name === 'quality-reviewer'">
        <div class="detail-label">能否进入下一阶段</div>
        <div class="gate-box" :class="{ ok: gateVerdict?.ok }">
          {{ gateVerdict?.text }}
          <div v-if="gate.summary" class="gate-sub">
            检查 {{ gate.summary.passed ?? '?' }}/{{ gate.summary.total }} 通过
          </div>
          <div v-for="(b, i) in (gate.blocking_issues || []).slice(0, 3)" :key="i" class="blocking-item">
            {{ b }}
          </div>
        </div>
      </template>

      <details v-if="engineer.name === 'case-generator'" class="skill-record">
        <summary>生成进程日志（尾部，3s 刷新）</summary>
        <LogTailBox :run-id="runId" :iteration="round" name="generation_console.log" title="generation_console.log" />
      </details>
      <details v-if="engineer.name === 'case-executor'" class="skill-record">
        <summary>执行日志（尾部，3s 刷新）</summary>
        <LogTailBox :run-id="runId" :iteration="round" name="execution.log" title="execution.log" />
      </details>

    </template>
    <template v-else>
      <h3>节点详情</h3>
      <p class="subtle">点击流程节点或机器人，在这里查看它的输入、技能、产物与交接。</p>
    </template>
    <ArtifactViewer ref="viewer" :run-id="runId" />
  </aside>
</template>

<style scoped>
/* 定义加载状态 / 权限配置 / 附加字段 */
.def-notice {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--wb-code-bg);
  color: var(--wb-muted);
  font-size: 11.5px;
  line-height: 1.6;
}
.def-notice-bad {
  background: var(--wb-red-soft);
  color: var(--wb-red);
}





.detail {
  background: var(--wb-card);
  border: 1px solid var(--wb-line);
  border-radius: 14px;
  padding: 18px;
  position: sticky;
  top: 20px;
  max-height: calc(100vh - 120px);
  max-height: calc(100dvh - 120px);
  overflow: auto;
}
/* 抽屉模式（窄屏）：取消 sticky 与边框 */
@media (max-width: 900px) {
  .detail { position: static; border: 0; border-radius: 0; max-height: none; padding: 14px 16px; }
}
h3 { font-size: 15px; margin: 0 0 14px; }
.node-heading { display: flex; align-items: center; gap: 12px; }
.node-heading b { font-size: 15px; display: block; }
.node-heading small { display: block; font-size: 11px; color: var(--wb-muted); }
.detail-badge {
  margin: 16px 0;
  background: var(--wb-blue-soft);
  color: var(--wb-blue);
  padding: 7px;
  text-align: center;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}
.badge-inline {
  display: inline-block;
  margin-left: 6px;
  padding: 0 5px;
  font-size: 9.5px;
  line-height: 14px;
  border-radius: 6px;
  background: var(--wb-line);
  color: var(--wb-muted);
  vertical-align: 1px;
}
.detail-label { color: var(--wb-muted); font-size: 11px; margin-top: 16px; }
.key-output {
  background: var(--wb-blue-soft);
  border-left: 3px solid var(--wb-blue);
  border-radius: 4px;
  padding: 12px;
  font-size: 12.5px;
  margin: 8px 0 0;
  line-height: 1.6;
}
.skill-chip {
  display: inline-block;
  font: 10px/1.5 ui-monospace, monospace;
  padding: 4px 7px;
  background: var(--wb-blue-soft);
  border: 1px solid var(--wb-blue-line);
  border-radius: 5px;
  margin: 4px 4px 0 0;
  overflow-wrap: anywhere;
  max-width: 100%;
  color: var(--wb-ink);
}
.subtle { color: var(--wb-muted); font-size: 12px; }
.tiny { font-size: 10px; margin: 4px 0 0; }
.skill-record { margin-top: 9px; font-size: 10px; border-top: 1px solid var(--wb-line); padding-top: 8px; }
.skill-record summary { cursor: pointer; font-size: 11px; color: var(--wb-blue); }
.skill-record ul { padding-left: 16px; }
.skill-record li { margin: 8px 0; overflow-wrap: anywhere; font-size: 11px; color: var(--wb-ink); }
.modules { margin-top: 6px; }
.files { margin-top: 7px; }
.file-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  background: var(--wb-code-bg);
  border-radius: 6px;
  margin-top: 5px;
}
.file-path {
  font-family: ui-monospace, monospace;
  font-size: 10px;
  overflow-wrap: anywhere;
  color: var(--wb-ink);
}
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
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12px;
  background: var(--wb-red-soft);
  color: var(--wb-red);
  border: 1px solid var(--wb-red-line);
}
.gate-box.ok { background: var(--wb-green-soft); color: var(--wb-green); border-color: var(--wb-green-line); }
.gate-sub { margin-top: 5px; font-size: 11px; color: var(--wb-muted); }
.blocking-item { margin-top: 4px; font-size: 11px; }

.detail { position:static; max-height:none; overflow:visible; }
.merged-kv { display:grid; grid-template-columns:minmax(110px,160px) minmax(0,1fr); gap:8px 16px; font-size:12px; }
.merged-kv dt { color:var(--wb-muted); }.merged-kv dd { margin:0; overflow-wrap:anywhere; }.merged-kv small { display:block; margin-top:4px; color:var(--wb-muted); }

</style>
