<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { useTheme } from '../composables/useTheme'
import RobotHead from '../components/band/RobotHead.vue'
import AssetCard from '../components/assets/AssetCard.vue'
import { assetTitle, chooseAgent, describeAsset, isRelated, relationDescription, relationFor, relationText, revealDetails, type Asset, type Catalog } from '../components/assets/model'

const router = useRouter()
const { theme, toggle } = useTheme()
const catalog = ref<Catalog | null>(null)
const ui = computed(() => catalog.value?.ui || {})
const loading = ref(true)
const error = ref(false)
const selectedAgent = ref('')
const selectedAsset = ref('')
const detailPanel = ref<HTMLElement>()
let originCard: HTMLElement | undefined
async function selectAsset(id: string, event: MouseEvent) {
  originCard = event.currentTarget as HTMLElement
  selectedAsset.value = id
  await nextTick()
  revealDetails(detailPanel.value, window.innerWidth)
}
async function closeDetail() {
  selectedAsset.value = ''
  await nextTick()
  if (window.innerWidth <= 850 && originCard?.isConnected) {
    originCard.scrollIntoView({ block: 'center' })
    originCard.focus({ preventScroll: true })
  }
}
const agent = computed(() => catalog.value?.agents.find(a => a.name === selectedAgent.value))
const allAssets = computed(() => catalog.value ? [...catalog.value.skills, ...catalog.value.knowledge] : [])
const detail = computed(() => allAssets.value.find(a => a.id === selectedAsset.value))
const summary = computed(() => !agent.value ? '' : agent.value.availability === 'ready' ? agent.value.description : ui.value.unavailable_description)
function relation(item: Asset) { return relationFor(agent.value, item) }
const relatedCount = computed(() => allAssets.value.filter(a => isRelated(relation(a))).length)
const families = computed(() => (catalog.value?.families || []).map(f => {
  const items = catalog.value?.knowledge.filter(a => a.family === f.id) || []
  return { ...f, count: items.length, groups: Object.entries(catalog.value?.scopes || {})
    .map(([scope, title]) => ({ scope, title, items: items.filter(a => a.scope === scope) }))
    .filter(g => g.items.length) }
}))
const detailFamily = computed(() => catalog.value?.families.find(f => f.id === detail.value?.family))
function selectAgent(name: string) { selectedAgent.value = name; selectedAsset.value = '' }
async function load() {
  if (loading.value && catalog.value) return
  loading.value = true; error.value = false
  try {
    const next = await api.assets()
    // Preserve context across content refreshes; fall back only for removed entries.
    if (!next.agents.some(a => a.name === selectedAgent.value)) selectedAgent.value = chooseAgent(next.agents, next.default_agent)
    if (![...next.skills, ...next.knowledge].some(a => a.id === selectedAsset.value)) selectedAsset.value = ''
    catalog.value = next
  } catch { error.value = true }
  finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div class="assets-page">
    <!-- Bootstrap messages must remain available when the sole content file is unreadable. -->
    <div v-if="!catalog && loading" class="empty-state" role="status">正在加载页面内容…</div>
    <div v-else-if="!catalog && error" class="empty-state" role="alert"><p>暂时无法加载页面内容，请稍后重试。</p><button class="action" @click="load">重试</button></div>
    <template v-if="catalog">
      <header class="topbar">
        <div class="brand"><span class="logo" aria-hidden="true"><i /><i /><i /><i /></span>{{ ui.brand }}</div>
        <nav :aria-label="ui.brand"><span class="active" aria-current="page">{{ ui.nav_assets }}</span><button @click="router.push('/')">{{ ui.nav_runtime }}</button></nav>
        <button class="refresh-content" :disabled="loading" @click="load">{{ loading ? ui.refreshing : ui.refresh }}</button>
        <button class="theme-toggle" @click="toggle" :aria-label="theme === 'dark' ? ui.theme_to_light : ui.theme_to_dark">{{ theme === 'dark' ? ui.theme_light : ui.theme_dark }}</button>
      </header>
      <div class="page-heading"><div><h1>{{ ui.title }}</h1><p>{{ ui.subtitle }}</p></div><div class="totals"><b>{{ catalog.agents.length }}</b> {{ ui.agents_unit }} · <b>{{ catalog.skills.length }}</b> {{ ui.skills_unit }} · <b>{{ catalog.knowledge.length }}</b> {{ ui.knowledge_unit }}</div></div>
      <div v-if="error" class="notice" role="alert">{{ ui.refresh_failed }}</div>
      <div class="agent-band" role="group" :aria-label="ui.select_agent">
        <button v-for="(item, i) in catalog.agents" :key="item.name" class="engineer" :class="{ selected: selectedAgent === item.name }" :aria-pressed="selectedAgent === item.name" @click="selectAgent(item.name)">
          <span class="sequence" aria-hidden="true">{{ String(i + 1).padStart(2, '0') }}</span>
          <RobotHead :agent="item.name" :color="item.color" :size="56" :active="selectedAgent === item.name" />
          <strong>{{ item.role }}</strong><span>{{ item.availability === 'ready' ? (selectedAgent === item.name ? ui.viewing : ui.view_capability) : ui.agent_unavailable }}</span>
        </button>
      </div>
      <main>
        <section v-if="agent" class="agent-summary" aria-live="polite">
          <div><span class="eyebrow">{{ ui.current_agent }}</span><h2>{{ agent.role }}</h2><p>{{ summary }}</p></div>
          <div v-if="agent.availability === 'ready'" class="outcomes"><div><span>{{ ui.when_to_use }}</span><p>{{ agent.when_to_use }}</p></div><div><span>{{ ui.outcome }}</span><p>{{ agent.outcome }}</p></div></div>
        </section>
        <div class="legend"><span><i class="primary" />{{ ui.legend_required }}</span><span><i class="allowed" />{{ ui.legend_allowed }}</span><span><i class="denied" />{{ ui.legend_unknown }}</span><span class="legend-note">{{ ui.legend_note }}</span></div>
        <div class="workspace">
          <div class="capabilities">
            <section class="panel"><div class="panel-heading"><h3>{{ ui.skills_title }}</h3><span>{{ catalog.skills.length }} {{ ui.item_unit }}</span></div>
              <div v-if="catalog.skills.length" class="skill-grid"><AssetCard v-for="item in catalog.skills" :key="item.id" :asset="item" :labels="catalog.relation_labels" :relation="relation(item)" :selected="selectedAsset === item.id" @select="selectAsset(item.id, $event)" /></div>
              <p v-else class="empty-copy">{{ ui.skills_empty }}</p>
            </section>
            <section class="panel"><div class="panel-heading"><h3>{{ ui.knowledge_title }}</h3><span>{{ catalog.knowledge.length }} {{ ui.item_unit }}</span></div>
              <p class="section-intro">{{ ui.knowledge_intro }}</p>
              <div class="knowledge-columns"><section v-for="f in families" :key="f.id" class="knowledge-family"><div class="family-heading"><h4>{{ f.title }}</h4><span>{{ f.count }} {{ ui.item_unit }}</span></div>
                <details v-for="group in f.groups" :key="group.scope" :open="f.expanded_scopes.includes(group.scope)"><summary>{{ group.title }} <span>{{ group.items.length }}</span></summary><div class="module-list"><AssetCard v-for="item in group.items" :key="item.id" :asset="item" :labels="catalog.relation_labels" :relation="relation(item)" :selected="selectedAsset === item.id" @select="selectAsset(item.id, $event)" /></div></details>
                <p v-if="!f.count" class="empty-copy">{{ ui.knowledge_empty }}</p>
              </section></div>
            </section>
          </div>
          <aside ref="detailPanel" class="detail-panel" tabindex="-1" :aria-label="ui.detail_label" aria-live="polite">
            <template v-if="detail">
              <span class="eyebrow">{{ detail.kind === 'knowledge' ? ui.detail_knowledge : ui.detail_skill }}</span><h2>{{ assetTitle(detail) }}</h2>
              <div class="relation-badge" :class="{ muted: !isRelated(relation(detail)) }">{{ relationText(relation(detail), catalog.relation_labels) }}</div>
              <h3>{{ ui.purpose }}</h3><p class="purpose">{{ describeAsset(detail) }}</p>
              <h3>{{ ui.relation_heading.replace('{agent}', agent?.role || ui.choose_agent) }}</h3><p>{{ relationDescription(relation(detail), catalog.relation_labels) }}</p>
              <template v-if="detail.kind === 'knowledge'"><h3>{{ ui.scope }}</h3><p>{{ detailFamily?.description }} · {{ catalog.scopes[detail.scope || ''] }}</p><p class="subtle">{{ ui.scope_note }}</p></template>
              <button class="back-detail" @click="closeDetail">{{ ui.back }}</button>
            </template>
            <template v-else><span class="eyebrow">{{ ui.overview }}</span><h2>{{ agent?.role || ui.choose_agent }}</h2><div class="relation-badge">{{ relatedCount }} {{ ui.related_unit }}</div><p>{{ summary }}</p><h3>{{ ui.how_to_view }}</h3><p>{{ ui.how_to_view_description }}</p><p class="subtle">{{ ui.unknown_note }}</p></template>
          </aside>
        </div>
      </main>
    </template>
  </div>
</template>

<style scoped src="../components/assets/assets.css"></style>
