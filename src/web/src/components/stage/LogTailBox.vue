<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { api } from '../../api/client'

/** 大日志尾部滚动框（3s 轮询追加，组件卸载/切换即停） */
const props = defineProps<{
  runId: string
  iteration: number
  name: string
  title?: string
}>()

const text = ref('')
const totalBytes = ref(0)
const error = ref<string | null>(null)
let timer: ReturnType<typeof setTimeout> | null = null

async function tick() {
  try {
    const data = await api.logTail(props.runId, props.iteration, props.name)
    text.value = data.text
    totalBytes.value = data.total_bytes
    error.value = null
  } catch (e: any) {
    error.value = e?.message || String(e)
  }
  timer = setTimeout(tick, 3000)
}

function start() {
  stop()
  tick()
}

function stop() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

watch(() => [props.runId, props.iteration, props.name], start, { immediate: true })
onBeforeUnmount(stop)
</script>

<template>
  <div class="log-box">
    <div class="log-head">
      <span>{{ title || name }}</span>
      <span class="bytes">共 {{ (totalBytes / 1024 / 1024).toFixed(2) }} MB · 尾部 16KB · 3s 刷新</span>
    </div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" />
    <pre v-else class="log-text">{{ text || '（空）' }}</pre>
  </div>
</template>

<style scoped>
.log-box { border: 1px solid var(--wb-line); border-radius: 8px; overflow: hidden; margin-top: 8px; }
.log-head {
  display: flex;
  justify-content: space-between;
  padding: 6px 10px;
  background: var(--wb-card-soft);
  font-size: 12px;
  color: var(--wb-muted);
}
.log-text {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 10px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--wb-ink);
  background: var(--wb-code-bg);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
