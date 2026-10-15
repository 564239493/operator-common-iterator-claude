import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // 相对路径 + hash 路由：任意子路径零配置部署（nginx 挂任意前缀都可用），无需按部署位置重新构建
  base: './',
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8420',
    },
  },
  test: {
    // vitest 只跑 src/ 下的 TS 行为测试；tests/*.test.cjs 是 node:test 套件，由
    // `node --test tests/*.test.cjs` 单独执行（npm run test 两者串联）
    include: ['src/**/*.test.ts'],
  },
})
