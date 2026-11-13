<script setup lang="ts">
import { computed } from 'vue';

/* 模块覆盖率 · 环形图
 *   每个模块（op / host / tiling / kernel）一个甜甜圈环：
 *   外→内三条轨道 = 函数 / 行 / 分支，实心弧 = 已覆盖部分（按覆盖率着色），浅底 = 未覆盖部分；
 *   环心 = 模块名，右侧列 = 三指标明细。与 ModuleTreemap 读取同一份 coverage 数据形状。
 */
const props = defineProps<{ coverage: any }>();

const METRICS = [
  { key: 'func', label: '函数', hints: ['函数'] },
  { key: 'line', label: '行', hints: ['行', '语句'] },
  { key: 'branch', label: '分支', hints: ['分支'] },
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

const SOLID_G = '#72cdb9', SOLID_A = '#fdcf87', SOLID_N = '#e3e8ef';
const RATE_G = '#2b8d6f', RATE_A = '#b0762b', RATE_N = '#8a9aa5';
const TRACK_BASE = '#e9edf2';
const INK = '#12333a';

const isUnc = (m: Mod) => m.covered === 0;
function solidOf(m: Mod) { return isUnc(m) ? SOLID_N : (m.rate >= 80 ? SOLID_G : SOLID_A); }
function rateColor(m: Mod) { return isUnc(m) ? RATE_N : (m.rate >= 80 ? RATE_G : RATE_A); }

function fmt(n: number) { return n != null ? n.toLocaleString('en-US') : '-'; }
function rateStr(m: Mod) {
  if (!m.total) return '0';
  if (!m.covered) return '未覆盖';
  return Math.round(m.rate) + '%';
}

const R = [26, 18, 10];        // 外→内：函数 / 行 / 分支
const SW = 5.5;                // 轨道线宽
const C = (r: number) => 2 * Math.PI * r;
function dash(m: Mod, r: number) {
  const len = C(r) * (m.rate / 100);
  return `${len} ${C(r)}`;
}

/* 每个模块一个环：三条指标轨道 */
const donuts = computed(() => {
  const cov = props.coverage || {};
  return MODULES.map((mod) => {
    const tracks = METRICS.map((met, i) => {
      const m = metricOf(cov[mod.key], met.hints);
      return { ...m, key: met.key, label: met.label, r: R[i], c: C(R[i]), dash: dash(m, R[i]) };
    });
    return { key: mod.key, label: mod.label, tracks };
  });
});
</script>

<template>
  <div class="md-panel">
    <div class="md-head">
      <span class="md-title">模块覆盖率</span>
      <span class="md-sub">环形图 · 外→内：函数 / 行 / 分支</span>
    </div>

    <div class="md-cards">
      <div v-for="d in donuts" :key="d.key" class="md-card">
        <svg class="md-svg" viewBox="0 0 76 76">
          <g v-for="t in d.tracks" :key="t.key">
            <!-- 未覆盖浅底轨道 -->
            <circle :cx="38" :cy="38" :r="t.r" fill="none" :stroke="TRACK_BASE" :stroke-width="SW" />
            <!-- 已覆盖实心弧 -->
            <circle :cx="38" :cy="38" :r="t.r" fill="none"
              :stroke="solidOf(t)" :stroke-width="SW" :stroke-dasharray="t.dash" transform="rotate(-90 38 38)" />
            <title>{{ t.label }}覆盖率 · {{ rateStr(t) }} ({{ fmt(t.covered) }}/{{ fmt(t.total) }})</title>
          </g>
          <text x="38" y="38" text-anchor="middle" dominant-baseline="central" font-size="10"
            :fill="INK" font-weight="700">{{ d.label }}</text>
        </svg>
        <div class="md-meta">
          <div v-for="t in d.tracks" :key="t.key" class="md-row">
            <i class="dot" :style="{ background: rateColor(t) }"></i>
            <span class="md-row-label">{{ t.label }}</span>
            <span class="md-row-rate" :style="{ color: rateColor(t) }">{{ rateStr(t) }}</span>
            <span class="md-row-num">{{ fmt(t.covered) }}/{{ fmt(t.total) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="md-foot">
      <span>实心弧表示已覆盖 · 浅底表示未覆盖</span>
      <span class="md-legend">
        <i class="dot" :style="{ background: '#72cdb9' }"></i>≥ 80%
        <i class="dot" :style="{ background: '#fdcf87' }"></i>&lt; 80%
        <i class="dot" :style="{ background: '#e3e8ef' }"></i>无数据
      </span>
    </div>
  </div>
</template>

<style scoped>
.md-panel { background: var(--wb-card); border: 1px solid var(--wb-line); border-radius: 12px; padding: 8px 10px; box-shadow: var(--wb-shadow); }
.md-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
.md-title { font-size: 15px; font-weight: 650; color: var(--wb-ink); }
.md-sub { font-size: 12px; color: var(--wb-muted); }
.md-cards { display: flex; flex-wrap: wrap; gap: 12px; }
.md-card { flex: 1 1 190px; min-width: 160px; display: flex; align-items: center; gap: 10px; }
.md-svg { width: 76px; height: 76px; flex: 0 0 auto; }
.md-meta { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.md-row { display: flex; align-items: baseline; gap: 5px; font-size: 12px; }
.md-row-label { color: var(--wb-muted); }
.md-row-rate { font-weight: 700; font-family: "JetBrains Mono", Consolas, monospace; }
.md-row-num { color: var(--wb-faint); font-family: "JetBrains Mono", Consolas, monospace; font-size: 11px; }
.md-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; font-size: 12px; color: var(--wb-muted); }
.md-legend { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
</style>