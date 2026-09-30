<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useTheme } from '../../composables/useTheme'

/**
 * 全站统一顶栏：品牌 + 页面导航 + 当前视图 + 右侧动作槽 + 明暗切换。
 * 四个视图共用同一实现（此前为四套复制样式，约束/覆盖率还是旧静态页遗留样式）。
 */
const props = defineProps<{ view: 'workbench' | 'assets' | 'constraints' | 'coverage' }>()
const router = useRouter()
const { theme, toggle } = useTheme()

const VIEW_LABELS = { workbench: '工作台', assets: '智能体资产', constraints: '约束审核', coverage: '覆盖率展示' } as const

function go(target: 'workbench' | 'assets') {
  router.push(target === 'workbench' ? '/' : '/assets')
}
</script>

<template>
  <header class="topbar">
    <div class="brand"><span class="logo" aria-hidden="true"><i /><i /><i /><i /></span>算子自主测试智能体工作台</div>
    <nav aria-label="页面">
      <span :class="{ active: view === 'workbench' }" @click="go('workbench')">工作台</span>
      <span :class="{ active: view === 'assets' }" @click="go('assets')">智能体资产</span>
      <!-- 非导航视图（约束审核/覆盖率）显示为当前所在位置，不可点 -->
      <span v-if="view !== 'workbench' && view !== 'assets'" class="active static">{{ VIEW_LABELS[view] }}</span>
    </nav>
    <div class="top-actions"><slot /></div>
    <button class="theme-toggle" :aria-label="theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'" @click="toggle">
      {{ theme === 'dark' ? '☀ 浅色' : '☾ 深色' }}
    </button>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  height: 72px;
  background: var(--wb-card);
  border-bottom: 1px solid var(--wb-line);
  display: flex;
  align-items: center;
  padding: 0 36px;
  gap: 42px;
  flex-shrink: 0;
}
.brand { font-size: 20px; font-weight: 750; letter-spacing: 0.5px; display: flex; align-items: center; white-space: nowrap; }
.logo { display: inline-grid; grid-template-columns: repeat(2, 9px); gap: 3px; margin-right: 12px; }
.logo i { width: 9px; height: 9px; border-radius: 2px; background: var(--wb-blue); }
nav { height: 100%; display: flex; align-items: center; gap: 30px; }
nav span { height: 100%; display: flex; align-items: center; color: var(--wb-muted); cursor: pointer; }
nav span.active { color: var(--wb-blue); border-bottom: 3px solid var(--wb-blue); font-weight: 650; }
nav span.static { cursor: default; }
.top-actions { margin-left: auto; display: flex; gap: 16px; align-items: center; min-width: 0; }
.theme-toggle { border: 0; background: transparent; color: var(--wb-ink); padding: 8px 0; white-space: nowrap; }
@media (max-width: 850px) {
  .topbar { padding: 0 16px; gap: 20px; }
  .brand { font-size: 15px; }
}
</style>
