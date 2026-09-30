<script setup lang="ts">
import { assetTitle, describeAsset, relationText, type Asset, type Relation, type RelationLabels } from './model'
defineProps<{ asset: Asset; relation: Relation; selected: boolean; labels: RelationLabels }>()
defineEmits<{ (e: 'select', event: MouseEvent): void }>()
</script>
<template>
  <button type="button" class="asset-card" :class="[relation, { selected }]" :aria-pressed="selected" @click="$emit('select', $event)">
    <strong>{{ assetTitle(asset) }}</strong>
    <span class="description">{{ describeAsset(asset) }}</span>
    <span class="relation">{{ relationText(relation, labels) }}</span>
  </button>
</template>
<style scoped>
.asset-card{display:flex;flex-direction:column;align-items:flex-start;gap:7px;width:100%;min-width:0;text-align:left;padding:13px;border:1px solid var(--wb-line);border-radius:8px;background:var(--wb-code-bg);color:var(--wb-muted);font:inherit;cursor:pointer;overflow-wrap:anywhere}
strong{font-size:13px;font-weight:600}.description{font-size:12px;line-height:1.7;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.relation{font-size:11px;margin-top:auto;padding-top:4px}
.required,.conditional,.reference{color:var(--wb-ink);background:var(--wb-blue-soft);border-color:var(--wb-blue-line)}.required{box-shadow:inset 3px 0 var(--wb-blue)}.required .relation,.conditional .relation,.reference .relation{color:var(--wb-blue)}
.denied{color:var(--wb-ink);background:var(--wb-red-soft);border-color:var(--wb-red-line);box-shadow:inset 3px 0 var(--wb-red)}.denied .relation{color:var(--wb-red)}
.selected{outline:2px solid var(--wb-blue);outline-offset:2px}.asset-card:hover{border-color:var(--wb-blue)}.asset-card:focus-visible{outline:2px solid var(--wb-blue);outline-offset:3px}
</style>
