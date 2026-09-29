import { createRouter, createWebHistory } from 'vue-router'
import RunRedirect from '../pages/RunRedirect.vue'
import RunPage from '../pages/RunPage.vue'
import AssetsPage from '../pages/AssetsPage.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/constraints', component: () => import('../pages/ConstraintsPage.vue') },
    { path: '/coverage', component: () => import('../pages/CoveragePage.vue') },
    { path: '/', component: RunRedirect },
    { path: '/run/:runId', component: RunPage, props: true },
    { path: '/assets', component: AssetsPage },
  ],
})
