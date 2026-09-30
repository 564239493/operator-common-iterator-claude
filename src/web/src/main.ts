import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/theme.css'
import App from './App.vue'
import { router } from './router'
import { withBase } from './paths'
import { useTask } from './composables/useTask'

// 相对路径 + hash 路由 = 任意子路径零配置部署；'./api/...' 相对于页面 URL 解析，
// 根路径与子路径行为一致，页面里的裸 fetch('/api/...') 调用点零改动。
{
  const originFetch = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) =>
    typeof input === 'string' && input.startsWith('/api/')
      ? originFetch(withBase(input), init)
      : originFetch(input, init)
}

// 兼容文档级旧式深链 /?run=<id>（hash 路由之前的地址形态）：种入共享选择后进应用
{
  const run = new URLSearchParams(window.location.search).get('run')
  if (run) useTask().seedRun(run)
}

// 主题初始化：localStorage 记忆，默认浅色
const saved = localStorage.getItem('wb-theme')
if (saved === 'dark') {
  document.documentElement.dataset.theme = 'dark'
  document.documentElement.classList.add('dark')
}

const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
