/**
 * 部署子路径支持：资源/接口引用一律相对化（vite base='./' + hash 路由），
 * nginx 挂任意前缀都零配置可用；根路径部署行为不变。
 */
export function withBase(path: string): string {
  // vite 保证 BASE_URL 以 / 结尾（此处为 './'）
  const base = import.meta.env.BASE_URL || '/'
  return base + (path.startsWith('/') ? path.slice(1) : path)
}
