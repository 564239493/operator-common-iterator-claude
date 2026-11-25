<script setup lang="ts">
import { computed } from 'vue';

/* 模块覆盖率 · 盒子图
 *   每个指标（行/分支/函数）一张卡片，2×2 摆放 op / host / tiling / kernel 四个模块盒子；
 *   盒子底色 ∝ 覆盖率（≥80% 绿 / 有数据橙 / 无数据灰），内含名称 + 百分比 + covered/total。
 *   与 ModuleTreemap 读取同一份 coverage（模块 metrics 数组）数据形状。
 */
const props = defineProps<{ coverage: any }>();

const METRICS = [
  { label: '行覆盖率', hints: ['行', '语句'] },
  { label: '分支覆盖率', hints: ['分支'] },
  { label: '函数覆盖率', hints: ['函数'] },
];
const MODULES = [
  { key: 'op', label: 'op' },
  { key: 'host', label: 'host' },
  { key: 'tiling', label: 'tiling' },
  { key: 'kernel', label: 'kernel' },
];

interface Mod { covered: number; total: number; rate: number }

function metricOf(mod: any, hints: string[]): Mod {
  const list = (mod && Array.isArray(mod.metrics)) ? mod.metrics : [];
  for (const m of list) {
    if (hints.some(h => (m.name || '').includes(h))) {
      return { covered: m.covered || 0, total: m.total || 0, rate: m.rate ?? 0 };
    }
  }
  return { covered: 0, total: 0, rate: 0 };
}

const TINT_G = '#e8f7f1', TINT_A = '#fdf1df', TINT_N = '#eef2f7';
const BORDER_G = '#bde6d5', BORDER_A = '#f1d6aa', BORDER_N = '#d5dde8';
const RATE_G = '#2b8d6f', RATE_A = '#b0762b', RATE_N = '#8a9aa5';

const isUnc = (m: Mod) => m.covered === 0;
function tintOf(m: Mod) { return isUnc(m) ? TINT_N : (m.rate >= 80 ? TINT_G : TINT_A); }
function borderOf(m: Mod) { return isUnc(m) ? BORDER_N : (m.rate >= 80 ? BORDER_G : BORDER_A); }
function rateColor(m: Mod) { return isUnc(m) ? RATE_N : (m.rate >= 80 ? RATE_G : RATE_A); }

function fmt(n: number) { return n != null ? n.toLocaleString('en-US') : '-'; }
function rateStr(m: Mod) {
  if (!m.total) return '0';
  if (!m.covered) return '未覆盖';
  return Math.round(m.rate) + '%';
}

/* 每指标一张卡片：四个模块盒子 */
const cards = computed(() => METRICS.map((met) => {
  const cov = props.coverage || {};
  const boxes = MODULES.map((mod) => {
    const m = metricOf(cov[mod.key], met.hints);
    return { key: mod.key, label: mod.label, ...m };
  });
  return { label: met.label, boxes };
}));
</script>

<template>
  <div class="cb-panel">
    <div class="cb-head">
      <span class="cb-title">模块覆盖率</span>
      <span class="cb-sub">盒子图 · 底色表示覆盖率</span>
    </div>

    <div class="cb-cards">
      <div v-for="card in cards" :key="card.label" class="cb-card">
        <div class="cb-card-title">{{ card.label }}</div>
        <div class="cb-grid">
          <div v-for="b in card.boxes" :key="b.key" class="cb-box"
            :style="{ background: tintOf(b), borderColor: borderOf(b) }"
            :title="`${b.label} · ${rateStr(b)} (${fmt(b.covered)}/${fmt(b.total)})`">
            <div class="cb-name">{{ b.label }}</div>
            <div class="cb-rate" :style="{ color: rateColor(b) }">{{ rateStr(b) }}</div>
            <div class="cb-num">{{ fmt(b.covered) }} / {{ fmt(b.total) }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="cb-foot">
      <span>模块：op / host / tiling / kernel</span>
      <span class="cb-legend">
        <i class="dot" :style="{ background: '#72cdb9' }"></i>≥ 80%
        <i class="dot" :style="{ background: '#fdcf87' }"></i>&lt; 80%
        <i class="dot" :style="{ background: '#e3e8ef' }"></i>无数据
      </span>
    </div>
  </div>
</template>

<style scoped>
.cb-panel { background: var(--wb-card); border: 1px solid var(--wb-line); border-radius: 12px; padding: 8px 10px; box-shadow: var(--wb-shadow); }
.cb-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
.cb-title { font-size: 15px; font-weight: 650; color: var(--wb-ink); }
.cb-sub { font-size: 12px; color: var(--wb-muted); }
.cb-cards { display: flex; flex-wrap: wrap; gap: 10px; }
.cb-card { flex: 1 1 240px; min-width: 0; }
.cb-card-title { font-size: 13px; color: var(--wb-muted); font-weight: 600; margin-bottom: 6px; }
.cb-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.cb-box { border: 1px solid; border-radius: 8px; padding: 6px 8px; min-height: 54px; }
.cb-name { font-size: 12px; color: var(--wb-muted); font-weight: 600; }
.cb-rate { font-size: 17px; font-weight: 750; font-family: "JetBrains Mono", Consolas, monospace; margin: 2px 0; }
.cb-num { font-size: 11px; color: var(--wb-faint); font-family: "JetBrains Mono", Consolas, monospace; }
.cb-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; font-size: 12px; color: var(--wb-muted); }
.cb-legend { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 3px; display: inline-block; margin-left: 6px; }
</style>