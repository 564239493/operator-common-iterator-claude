<script setup lang="ts">
import { computed } from 'vue';

/* 模块覆盖率 · 环带图
 *   每个指标（行/分支/函数）一组同心环：外→内 = op / host / tiling / kernel 四个模块，
 *   每环实心弧 = 已覆盖部分（按覆盖率着色），浅底 = 未覆盖部分；环心 = 该指标整体 %（op 模块）。
 *   与 ModuleTreemap 读取同一份 coverage（模块 metrics 数组）数据形状。
 */
const props = defineProps<{ coverage: any }>();

const METRICS = [
  { key: 'line', label: '行覆盖率', hints: ['行', '语句'] },
  { key: 'branch', label: '分支覆盖率', hints: ['分支'] },
  { key: 'func', label: '函数覆盖率', hints: ['函数'] },
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

const R = [29, 22, 15, 8];     // 外→内：op / host / tiling / kernel
const SW = 5.5;
const C = (r: number) => 2 * Math.PI * r;
function dash(m: Mod, r: number) {
  const len = C(r) * (m.rate / 100);
  return `${len} ${C(r)}`;
}

/* 每指标一组同心环：四个模块 */
const rings = computed(() => {
  const cov = props.coverage || {};
  return METRICS.map((met) => {
    const mods = MODULES.map((mod, i) => {
      const m = metricOf(cov[mod.key], met.hints);
      return { ...m, key: mod.key, label: mod.label, r: R[i], c: C(R[i]), dash: dash(m, R[i]) };
    });
    const overall = metricOf(cov.op, met.hints);
    return { key: met.key, label: met.label, mods, overall };
  });
});
</script>

<template>
  <div class="mr-panel">
    <div class="mr-head">
      <span class="mr-title">模块覆盖率</span>
      <span class="mr-sub">环带图 · 外→内：op / host / tiling / kernel</span>
    </div>

    <div class="mr-cards">
      <div v-for="g in rings" :key="g.key" class="mr-card">
        <div class="mr-card-title">{{ g.label }}</div>
        <div class="mr-body">
          <svg class="mr-svg" viewBox="0 0 76 76">
            <g v-for="m in g.mods" :key="m.key">
              <circle :cx="38" :cy="38" :r="m.r" fill="none" :stroke="TRACK_BASE" :stroke-width="SW" />
              <circle :cx="38" :cy="38" :r="m.r" fill="none"
                :stroke="solidOf(m)" :stroke-width="SW" :stroke-dasharray="m.dash" transform="rotate(-90 38 38)" />
              <title>{{ m.label }} · {{ rateStr(m) }} ({{ fmt(m.covered) }}/{{ fmt(m.total) }})</title>
            </g>
            <text x="38" y="38" text-anchor="middle" dominant-baseline="central" font-size="11"
              :fill="INK" font-weight="750">{{ Math.round(g.overall.rate) }}%</text>
          </svg>
          <div class="mr-meta">
            <div v-for="m in g.mods" :key="m.key" class="mr-row">
              <i class="dot" :style="{ background: rateColor(m) }"></i>
              <span class="mr-row-label">{{ m.label }}</span>
              <span class="mr-row-rate" :style="{ color: rateColor(m) }">{{ rateStr(m) }}</span>
              <span class="mr-row-num">{{ fmt(m.covered) }}/{{ fmt(m.total) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="mr-foot">
      <span>实心弧 = 已覆盖 · 浅底 = 未覆盖 · 环心 = 整体 %</span>
      <span class="mr-legend">
        <i class="dot" :style="{ background: '#72cdb9' }"></i>≥ 80%
        <i class="dot" :style="{ background: '#fdcf87' }"></i>&lt; 80%
        <i class="dot" :style="{ background: '#e3e8ef' }"></i>无数据
      </span>
    </div>
  </div>
</template>

<style scoped>
.mr-panel { background: var(--wb-card); border: 1px solid var(--wb-line); border-radius: 12px; padding: 8px 10px; box-shadow: var(--wb-shadow); }
.mr-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
.mr-title { font-size: 15px; font-weight: 650; color: var(--wb-ink); }
.mr-sub { font-size: 12px; color: var(--wb-muted); }
.mr-cards { display: flex; flex-wrap: wrap; gap: 12px; }
.mr-card { flex: 1 1 250px; min-width: 200px; }
.mr-card-title { font-size: 13px; color: var(--wb-muted); font-weight: 600; margin-bottom: 6px; }
.mr-body { display: flex; align-items: center; gap: 10px; }
.mr-svg { width: 76px; height: 76px; flex: 0 0 auto; }
.mr-meta { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.mr-row { display: flex; align-items: baseline; gap: 5px; font-size: 12px; }
.mr-row-label { color: var(--wb-muted); }
.mr-row-rate { font-weight: 700; font-family: "JetBrains Mono", Consolas, monospace; }
.mr-row-num { color: var(--wb-faint); font-family: "JetBrains Mono", Consolas, monospace; font-size: 11px; }
.mr-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; font-size: 12px; color: var(--wb-muted); }
.mr-legend { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
</style>