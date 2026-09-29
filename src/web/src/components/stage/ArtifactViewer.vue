<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../../api/client'

/** 产物原始 JSON 查看弹窗（走 /artifact 白名单端点） */
const props = defineProps<{ runId: string }>()

const visible = ref(false)
const loading = ref(false)
const error = ref<string | null>(null)
const payload = ref<any>(null)
const currentPath = ref('')

async function open(path: string) {
  currentPath.value = path
  visible.value = true
  loading.value = true
  error.value = null
  payload.value = null
  try {
    payload.value = await api.artifact(props.runId, path)
  } catch (e: any) {
    error.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

defineExpose({ open })

watch(visible, (v) => {
  if (!v) payload.value = null
})
</script>

<template>
  <el-dialog v-model="visible" :title="currentPath" width="72%" top="6vh" append-to-body>
    <div v-if="loading" v-loading="true" style="height: 200px" />
    <el-alert v-else-if="error" type="error" :title="error" :closable="false" />
    <template v-else-if="payload">
      <div class="meta">
        <el-tag size="small" effect="plain">大小 {{ (payload.size / 1024).toFixed(1) }} KB</el-tag>
        <el-tag size="small" effect="plain" type="info">sha256 {{ payload.sha256.slice(0, 12) }}…</el-tag>
        <el-tag v-if="payload.truncated" size="small" type="warning">已截断（长列表/长字符串仅显示样本）</el-tag>
        <el-tag v-if="payload.json?._error" size="small" type="danger">{{ payload.json._error }}</el-tag>
      </div>
      <pre class="json-view">{{ payload.json_text }}</pre>
    </template>
  </el-dialog>
</template>

<style scoped>
.meta { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.json-view {
  margin: 0;
  max-height: 62vh;
  overflow: auto;
  background: var(--wb-code-bg);
  border: 1px solid var(--wb-line);
  border-radius: 8px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.55;
  color: var(--wb-ink);
}
</style>
