import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { api } from '../api/client'
import { usePolling } from './usePolling'
import { groupRunsByOperator, pickDefaultRun, type OperatorGroup } from '../components/task/group'
import type { RunSummary } from '../api/types'

/**
 * 全站共享的任务选择状态（模块级单例，模式同 useTheme）。
 * 任务身份 = 算子名 + 一次测试运行（runs/ 目录名），固定视图 URL（/run 等）下
 * 由页面内 TaskPicker 切换；跨页与刷新经 localStorage 保持。
 * 纯模块单例、不依赖 app 上下文 —— router 守卫需在 setup 外调用（勿引入 pinia）。
 * 注意：HMR 下模块重求值会重开轮询定时器，仅 dev 现象，接受。
 */
const STORAGE_KEY = 'wb-task-run'

// manual：模块级使用无组件实例，onMounted 不会触发，须显式 start（见 usePolling 注释）
const poll = usePolling<RunSummary[]>(() => api.runs(), 15000, { manual: true })
const selectedRunId = ref<string>(typeof localStorage === 'undefined' ? '' : localStorage.getItem(STORAGE_KEY) || '')
const ready = ref(false)
let firstLoad: Promise<void> | null = null

function persist(id: string) {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    /* 隐私模式等存储失败不阻断选择 */
  }
}

/** 用户显式选择（TaskPicker）：改本次会话并持久化 */
function selectRun(id: string) {
  selectedRunId.value = id
  persist(id)
}

/** 一次性引导（?run= 深链 / /run/:runId 兼容 redirect）：只改本次会话，不写持久化、不等列表 */
function seedRun(id: string) {
  if (id) selectedRunId.value = id
}

/** 首次任务列表取数完成后 resolve（成功或失败都算就绪；失败由页面按 loadError 提示） */
function waitForReady(): Promise<void> {
  if (ready.value) return Promise.resolve()
  if (!firstLoad) {
    firstLoad = new Promise(resolve => {
      const stopWatch = watch([poll.data, poll.error], () => {
        if (poll.data.value !== null || poll.error.value !== null) {
          ready.value = true
          stopWatch()
          resolve()
        }
      })
    })
  }
  return firstLoad
}

// 校验性自动选择：列表到达后，空选择或已消失的选择回退到最新任务并持久化；
// 仍存在的显式/持久化选择（含 parse_error 的，页面能渲染其错误）不覆盖。
watch(poll.data, list => {
  if (!Array.isArray(list) || !list.length) return
  const ids = new Set(list.map(r => r.run_id))
  if (!selectedRunId.value || !ids.has(selectedRunId.value)) {
    const def = pickDefaultRun(list)
    if (def) {
      selectedRunId.value = def.run_id
      persist(def.run_id)
    }
  }
})

// 模块初始化即取数并启动 15s 轮询；标签页隐藏暂停、可见恢复（manual 模式自管可见性）
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) poll.stop()
    else poll.start()
  })
}
poll.start()
void waitForReady()

export function useTask(): {
  runs: ComputedRef<RunSummary[]>
  loadError: Ref<string | null>
  loading: Ref<boolean>
  ready: Ref<boolean>
  selectedRunId: Ref<string>
  selectedRun: ComputedRef<RunSummary | null>
  groups: ComputedRef<OperatorGroup[]>
  selectRun: (id: string) => void
  seedRun: (id: string) => void
  waitForReady: () => Promise<void>
} {
  const runs = computed(() => poll.data.value || [])
  return {
    runs,
    loadError: poll.error,
    loading: poll.loading,
    ready,
    selectedRunId,
    selectedRun: computed(() => runs.value.find(r => r.run_id === selectedRunId.value) || null),
    groups: computed(() => groupRunsByOperator(runs.value)),
    selectRun,
    seedRun,
    waitForReady,
  }
}
