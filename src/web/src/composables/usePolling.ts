import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/**
 * 通用轮询 composable：
 * - setTimeout 链（非 setInterval，防重入）
 * - 页面隐藏时暂停
 * - interval 可为函数，按最新数据动态决定下次间隔
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  interval: number | ((data: T | null) => number),
  options: { immediate?: boolean } = {},
) {
  const data: Ref<T | null> = ref(null)
  const error: Ref<string | null> = ref(null)
  const loading = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = true

  const nextDelay = () =>
    typeof interval === 'function' ? interval(data.value) : interval

  async function tick() {
    if (stopped) return
    loading.value = true
    try {
      data.value = await fetcher()
      error.value = null
    } catch (e: any) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
    if (!stopped) timer = setTimeout(tick, nextDelay())
  }

  function start() {
    if (!stopped) return
    stopped = false
    tick()
  }

  function stop() {
    stopped = true
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function onVisibility() {
    if (document.hidden) {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
    } else if (!stopped && !timer) {
      timer = setTimeout(tick, 200)
    }
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    if (options.immediate !== false) start()
  })
  onBeforeUnmount(() => {
    stop()
    document.removeEventListener('visibilitychange', onVisibility)
  })

  return { data, error, loading, start, stop, refresh: tick }
}
