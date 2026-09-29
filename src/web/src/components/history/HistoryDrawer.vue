<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { usePolling } from '../../composables/usePolling'
import { api } from '../../api/client'
import type { RunSummary } from '../../api/types'

/** 历史任务抽屉（顶部 ◷ 入口触发） */
const props = defineProps<{ currentRunId?: string }>()
const router = useRouter()
const visible = ref(false)

const { data: runs } = usePolling<RunSummary[]>(() => api.runs(), 15000)

function open() {
  visible.value = true
}

function goto(run: RunSummary) {
  if (run.parse_error) return
  visible.value = false
  router.push(`/run/${encodeURIComponent(run.run_id)}`)
}

defineExpose({ open })

function stateType(state?: string) {
  if (!state) return 'info'
  if (state === 'SUCCESS') return 'success'
  if (state.startsWith('STOP') || state === 'BLOCKED') return 'danger'
  if (state === 'MAX_ITERATIONS') return 'warning'
  return 'primary'
}

function fmt(at?: string) {
  if (!at) return '—'
  const d = new Date(at)
  return isNaN(d.getTime()) ? at : d.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <el-drawer v-model="visible" title="历史任务" size="420px" direction="rtl">
    <div v-if="!(runs || []).length" class="empty">暂无历史任务</div>
    <div
      v-for="r in runs || []"
      :key="r.run_id"
      class="run-item"
      :class="{ current: r.run_id === props.currentRunId, broken: !!r.parse_error }"
      @click="goto(r)"
    >
      <div class="line1">
        <b>{{ r.operator || r.run_id }}</b>
        <el-tag v-if="r.state" size="small" :type="stateType(r.state)" effect="light">{{ r.state }}</el-tag>
        <el-tag v-if="r.parse_error" size="small" type="danger" effect="plain">解析失败</el-tag>
      </div>
      <div class="line2">{{ r.run_id }}</div>
      <div class="line3">
        <span>{{ r.test_framework }} · {{ r.mode }}</span>
        <span>第 {{ r.current_iteration ?? '—' }}/{{ r.max_iterations ?? '—' }} 轮</span>
        <span>{{ fmt(r.last_activity || r.created_at) }}</span>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.empty { color: var(--wb-faint); text-align: center; padding: 30px 0; }
.run-item {
  padding: 10px 12px;
  border: 1px solid var(--wb-line);
  border-radius: 10px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: background 0.15s;
  background: var(--wb-card);
}
.run-item:hover { background: var(--wb-hover); }
.run-item.current { border-color: var(--wb-blue); background: var(--wb-blue-soft); }
.run-item.broken { opacity: 0.55; cursor: not-allowed; }
.line1 { display: flex; align-items: center; gap: 8px; font-size: 13.5px; color: var(--wb-ink); }
.line2 { color: var(--wb-faint); font-size: 11px; margin-top: 3px; word-break: break-all; }
.line3 { display: flex; gap: 12px; color: var(--wb-muted); font-size: 11px; margin-top: 5px; flex-wrap: wrap; }
</style>
