/**
 * 覆盖率树（ops_final_cov_report）的数据判定与渲染数据转换。
 * 纯函数、无 Vue 依赖，vitest 直接覆盖（模式同 components/task/group.ts）。
 *
 * 「有数据」的判定：节点带 summary。
 *   - 有自身覆盖率 json（merged/leaf）→ 后端给出 summary；
 *   - 自身没有 json、但有带数据的子级（agg 中间层，如某库/域缺本级汇总报告）→ 后端用子级聚合出 summary；
 *   - 纯占位目录（只有 .gitkeep，整枝都没有数据）→ 没有 summary，即「无数据」。
 *
 * 渲染约定（对应需求）：
 *   - 树形图只展示有数据的节点 → onlyCovData；
 *   - 下拉框中无数据节点默认收起、可展开查看子级、但不可选中 → toCovTreeSelectData 打 disabled。
 */

/** 节点是否带覆盖率数据（自身报告或由子级聚合而来） */
export function hasCovData(n: any): boolean {
  return !!(n && n.summary)
}

/**
 * 仅保留有数据的节点：无数据节点连同其子树一起剔除（无数据节点的子级同样无数据）。
 * 返回浅拷贝节点（源树在页面导航中被复用，不能被改写），折叠态按路径记录，不受影响。
 */
export function onlyCovData(nodes: any[] | null | undefined): any[] {
  const out: any[] = []
  for (const n of nodes || []) {
    if (!hasCovData(n)) continue
    const kids = Array.isArray(n.children) ? onlyCovData(n.children) : []
    out.push({ ...n, children: kids })
  }
  return out
}

/**
 * 转 el-tree-select 数据：每节点 value = 层级路径（与页面 path.join('/') 一致，node-key 取 value），
 * 并透传原节点的 summary/kind/has_analysis/version 等字段供下拉渲染使用。
 * 无数据节点标记 disabled：仍可展开（el-tree 的 +/− 由用户主动点击），但点击不会选中
 * （el-tree-select 的 onNodeClick 会跳过 disabled 节点）。
 */
export function toCovTreeSelectData(n: any, prefix: string[] = []): any {
  const path = [...prefix, n.name]
  const node: any = { ...n, name: n.name, value: path.join('/') }
  if (!hasCovData(n)) node.disabled = true
  const kids = Array.isArray(n.children) ? n.children : []
  if (kids.length) node.children = kids.map((c: any) => toCovTreeSelectData(c, path))
  else delete node.children // 叶子不保留空 children 键（el-tree 以无 children/空数组判叶子）
  return node
}

/** 层级下拉（面包屑算子/产品层）选项：保留全部节点，无数据节点置灰不可选 */
export function toLayerOptions(nodes: any[] | null | undefined): { name: string; disabled: boolean }[] {
  return (nodes || []).map((n: any) => ({ name: n && n.name ? n.name : '', disabled: !hasCovData(n) }))
}