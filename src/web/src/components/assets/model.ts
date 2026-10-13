/** Rendering mechanics only. All displayed content comes from /api/assets. */
export type Relation = 'required' | 'conditional' | 'reference' | 'denied' | 'unmentioned' | 'unavailable'
export type RelationLabels = Record<Relation, { label: string; description: string }>
export interface Asset {
  id: string; name: string; title: string; description: string; kind: 'skill' | 'knowledge'
  availability: string; family?: string; scope?: string
}
export interface AssetAgent {
  name: string; role: string; description: string; color: string; when_to_use: string; outcome: string
  availability: string; relations: Record<string, Relation>
}
export interface Catalog {
  schema_version: number; content_version: string; default_agent: string; default_family: string
  agents: AssetAgent[]; skills: Asset[]; knowledge: Asset[]
  families: { id: string; title: string; description: string; expanded_scopes: string[] }[]
  scopes: Record<string, string>; ui: Record<string, string>; relation_labels: RelationLabels
}
export function assetTitle(item: Asset) { return item.title }
export function describeAsset(item: Asset) { return item.description }
export function revealDetails(panel: HTMLElement | undefined, viewportWidth: number) {
  if (panel && viewportWidth <= 850) {
    panel.scrollIntoView({ block: 'start' })
    panel.focus({ preventScroll: true })
  }
}
export function chooseAgent(agents: AssetAgent[], preferred = '') {
  return (agents.find(a => a.name === preferred && a.availability === 'ready') || agents.find(a => a.availability === 'ready') || agents[0])?.name || ''
}
/**
 * 各知识族并列展示：关系按智能体与资产的真实关联返回，不再按所选 family 降级
 * （out_of_scope 派生已随 family 下拉移除而取消；denied/unmentioned 均为内容源明示）。
 */
export function relationFor(agent: AssetAgent | undefined, item: Asset): Relation {
  if (item.availability !== 'ready') return 'unavailable'
  if (!agent || agent.availability !== 'ready') return 'unmentioned'
  return Object.prototype.hasOwnProperty.call(agent.relations, item.id) ? agent.relations[item.id] : 'unmentioned'
}
export function isRelated(relation: Relation) { return ['required', 'conditional', 'reference'].includes(relation) }
export function relationText(relation: Relation, labels: RelationLabels) { return labels[relation].label }
export function relationDescription(relation: Relation, labels: RelationLabels) { return labels[relation].description }
