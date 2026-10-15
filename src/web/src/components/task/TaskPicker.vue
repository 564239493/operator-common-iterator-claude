<script setup lang="ts">
import { computed } from 'vue'
import { useTask } from '../../composables/useTask'
import { fmtTaskOption, fmtTaskTime, stateTagType } from './group'

/**
 * 全站统一的任务选择器：按算子分组，组内为该算子的一次次测试运行（按创建时间倒序）。
 * 组件内部持有共享 store 的读写，页面不跨 API 范式做 v-model。
 */
const props = defineProps<{ disabled?: boolean }>()
const { runs, selectedRunId, selectedRun, groups, selectRun } = useTask()

const value = computed({
  get: () => selectedRunId.value,
  set: (id: string) => selectRun(id),
})
const current = computed(() => selectedRun.value)
const frameworkText = (r: { test_framework?: string; mode?: string }) =>
  `${r.test_framework || '—'} · ${r.mode || '—'}`
</script>

<template>
  <el-select
    v-model="value"
    class="task-picker"
    filterable
    placeholder="选择任务"
    :disabled="props.disabled"
    data-test="task-picker"
  >
    <template #label>
      <span v-if="current" class="picked">
        <b>{{ current.operator || current.run_id }}</b>
        <span class="picked-time">{{ fmtTaskTime(current.created_at) }}</span>
        <span class="picked-state">{{ current.state || '…' }}</span>
      </span>
    </template>
    <el-option-group
      v-for="g in groups"
      :key="g.operator"
      :label="`${g.operator}（${g.runs.length}）`"
    >
      <el-option
        v-for="r in g.runs"
        :key="r.run_id"
        :value="r.run_id"
        :label="fmtTaskOption(r)"
        :title="r.run_id"
      >
        <div class="task-option">
          <span class="task-time">{{ fmtTaskTime(r.created_at) }}</span>
          <el-tag v-if="r.state" size="small" :type="stateTagType(r.state)" effect="light">{{ r.state }}</el-tag>
          <span class="task-meta">第 {{ r.current_iteration ?? '—' }}/{{ r.max_iterations ?? '—' }} 轮</span>
          <span class="task-meta meta-dim">{{ frameworkText(r) }}</span>
        </div>
      </el-option>
    </el-option-group>
    <el-option v-if="!groups.length && !runs.length" value="" label="暂无可解析的任务" disabled />
  </el-select>
</template>

<style scoped>
.task-picker { width: 320px; }
.picked { display: inline-flex; align-items: baseline; gap: 8px; min-width: 0; }
.picked b { color: var(--wb-ink); font-weight: 600; }
.picked-time { color: var(--wb-muted); font-size: 12px; }
.picked-state { color: var(--wb-faint); font-size: 12px; }
.task-option { display: flex; align-items: center; gap: 8px; min-width: 0; }
.task-time { color: var(--wb-ink); font-variant-numeric: tabular-nums; }
.task-meta { color: var(--wb-muted); font-size: 12px; white-space: nowrap; }
.meta-dim { color: var(--wb-faint); margin-left: auto; }
</style>
