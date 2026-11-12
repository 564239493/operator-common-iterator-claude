<script setup lang="ts">
import MetricBar from './MetricBar.vue';

defineProps<{
  title: string;
  meta: string;
  func: number;
  line: number;
  branch: number;
  badge?: string;
  badgeType?: 'cov-only' | 'has-md' | 'no-cov';
  sub?: string;
}>();
defineEmits<{ click: [] }>();
</script>

<template>
  <div class="card" @click="$emit('click')">
    <div class="c-head">
      <div class="c-name">{{ title }}</div>
      <span v-if="badge" class="badge" :class="badgeType">{{ badge }}</span>
    </div>
    <MetricBar name="函数覆盖率" :rate="func" />
    <MetricBar name="行覆盖率" :rate="line" />
    <MetricBar name="分支覆盖率" :rate="branch" />
    <div v-if="sub" class="c-sub">{{ sub }}</div>
  </div>
</template>

<style scoped>
.card { background: var(--wb-card); border: 1px solid var(--wb-line); border-radius: 12px; padding: 16px 18px; cursor: pointer;
  box-shadow: var(--wb-shadow); transition: border-color .15s, box-shadow .15s, transform .15s; }
.card:hover { border-color: var(--wb-blue-line); box-shadow: 0 6px 18px rgba(39,116,237,.10); transform: translateY(-2px); }
.c-head { display: flex; align-items: center; gap: 8px; }
.c-name { font-size: 15px; font-weight: 650; color: var(--wb-ink); min-width: 0; }
.c-meta { font-size: 12px; color: var(--wb-faint); margin: 2px 0 10px; }
.c-sub { font-size: 12px; color: var(--wb-muted); margin-top: 8px; }
.badge { font-size: 11px; padding: 0 8px; border-radius: 9px; white-space: nowrap; flex-shrink: 0; }
.badge.cov-only { background: var(--wb-code-bg); color: var(--wb-muted); }
.badge.has-md { background: var(--wb-green-soft); color: var(--wb-green); }
.badge.no-cov { background: var(--wb-red-soft); color: var(--wb-red); }
</style>