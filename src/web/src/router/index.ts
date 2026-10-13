import { createRouter, createWebHashHistory } from 'vue-router'
import RunPage from '../pages/RunPage.vue'
import AssetsPage from '../pages/AssetsPage.vue'
import { useTask } from '../composables/useTask'

/** ?run= 深链一次性引导：种进共享任务状态，不写持久化（raise_dashboard / 旧链接兼容） */
function seedFromQuery(to: { query: Record<string, unknown> }) {
  const run = to.query.run
  if (typeof run === 'string' && run) useTask().seedRun(run)
}

export const router = createRouter({
  // hash 路由：与部署前缀完全解耦（子路径/根路径零配置），页面 URL 形如 /前缀/#/run
  history: createWebHashHistory(),
  routes: [
    // 字符串 redirect 保留查询参数：/?run=X&iter=Y → /run?run=X&iter=Y，再由 seedFromQuery 引导
    { path: '/', redirect: '/run' },
    { path: '/run', component: RunPage, beforeEnter: seedFromQuery },
    // 兼容旧地址 /run/:runId：种进共享状态后跳固定地址，redirect 不进历史栈
    {
      path: '/run/:runId',
      redirect: to => {
        useTask().seedRun(String(to.params.runId))
        return { path: '/run', query: {} }
      },
    },
    { path: '/constraints', component: () => import('../pages/ConstraintsPage.vue'), beforeEnter: seedFromQuery },
    { path: '/coverage', component: () => import('../pages/CoveragePage.vue'), beforeEnter: seedFromQuery },
    { path: '/assets', component: AssetsPage },
  ],
})
