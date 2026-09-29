<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import type { RunSummary } from '../api/types'

/** / → 重定向到最新 run；无 run 时给提示 */
const router = useRouter()
const error = ref<string | null>(null)
const empty = ref(false)

onMounted(async () => {
  try {
    const runs: RunSummary[] = await api.runs()
    const first = runs.find((r) => !r.parse_error)
    if (first) {
      router.replace(`/run/${encodeURIComponent(first.run_id)}`)
    } else {
      empty.value = true
    }
  } catch (e: any) {
    error.value = e?.message || String(e)
  }
})
</script>

<template>
  <div class="redirect">
    <el-alert v-if="error" type="error" :title="`服务不可达：${error}`" :closable="false" />
    <el-empty v-else-if="empty" description="runs/ 目录下没有可解析的任务" />
    <div v-else v-loading="true" style="height: 200px" />
  </div>
</template>

<style scoped>
.redirect { padding: 60px 30px; }
</style>
