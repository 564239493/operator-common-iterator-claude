import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/theme.css'
import App from './App.vue'
import { router } from './router'

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
