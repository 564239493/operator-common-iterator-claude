<script setup lang="ts">
import CoverageCard from './CoverageCard.vue';

defineProps<{ title: string; tag?: string; items: any[] }>();
defineEmits<{ select: [item: any] }>();
</script>

<template>
  <div class="grid" v-if="items.length">
    <CoverageCard v-for="it in items" :key="it.key"
      :title="it.title" :badge="it.badge" :badge-type="it.badgeType" :meta="it.meta"
      :func="it.func" :line="it.line" :branch="it.branch" :sub="it.sub"
      @click="$emit('select', it)" />
  </div>
  <div v-else class="cov-empty">暂无覆盖率数据</div>
</template>

<style scoped>
.sec-title { font-size: 15px; font-weight: 650; color: var(--wb-ink); margin: 4px 0 12px; }
.sec-title .tag { font-size: 11px; color: var(--wb-muted); font-weight: 400; margin-left: 8px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.cov-empty { text-align: center; color: var(--wb-faint); padding: 80px 0; font-size: 14px; }
</style>
