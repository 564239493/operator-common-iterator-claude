import { computed, ref, type Ref } from 'vue'
import { api } from '../api/client'
import type { AgentDef, AgentRuntime } from '../api/types'

/** 工程师带数据：静态定义（/api/agents）+ 动态状态（runView.agents）合并 */
export function useEngineers(runView: Ref<any | null>) {
  const defs = ref<AgentDef[]>([])
  const defsError = ref<string | null>(null)

  api.agents()
    .then((list) => (defs.value = list))
    .catch((e) => (defsError.value = e?.message || String(e)))

  const engineers = computed(() => {
    const runtime: AgentRuntime[] = runView.value?.agents || []
    const byName = new Map(runtime.map((a) => [a.name, a]))
    return defs.value.map((def) => ({
      ...def,
      runtime: byName.get(def.name) || null,
    }))
  })

  return { defs, defsError, engineers }
}

/** agent frontmatter color → 十六进制色板 */
const COLOR_MAP: Record<string, string> = {
  blue: '#409eff',
  red: '#f56c6c',
  green: '#67c23a',
  orange: '#e6a23c',
  purple: '#9b59b6',
  cyan: '#17a2b8',
  pink: '#e84393',
  yellow: '#f1c40f',
  teal: '#20c997',
  grey: '#909399',
  gray: '#909399',
}

export function agentColor(color?: string): string {
  if (!color) return '#409eff'
  if (color.startsWith('#')) return color
  return COLOR_MAP[color.toLowerCase()] || '#409eff'
}
