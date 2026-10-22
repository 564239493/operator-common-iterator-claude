<script setup lang="ts">
import { computed } from 'vue';

/* 模块覆盖率 · 嵌套矩形树图
 *   op（外框）→ host（框内再嵌 tiling / host·其余）+ kernel
 *   面积 ∝ 总量；容器用浅色底 + 边框，叶子用实心色块；颜色 ∝ 覆盖率。
 *   三个指标各一张：行 / 分支 / 函数。
 */
const props = defineProps<{ coverage: any }>();

const METRICS = [
  { label: '行覆盖率', hints: ['行', '语句'] },
  { label: '分支覆盖率', hints: ['分支'] },
  { label: '函数覆盖率', hints: ['函数'] },
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

const GREEN = '#72cdb9', AMBER = '#fdcf87';
const TINT_G = '#e8f7f1', TINT_A = '#fdf1df', TINT_N = '#eef2f7';
const SOLID_G = '#72cdb9', SOLID_A = '#fdcf87', SOLID_N = '#e3e8ef';
const BORDER_G = '#bde6d5', BORDER_A = '#f1d6aa', BORDER_N = '#d5dde8';
const INK = '#12333a', SUB = '#8a9aa5';
const LINE_G = '#5bb8a6', LINE_A = '#eeb45c', LINE_N = '#cfd6e2';

const isUnc = (m: Mod) => m.covered === 0;
function tintOf(m: Mod) { return isUnc(m) ? TINT_N : (m.rate >= 80 ? TINT_G : TINT_A); }
function solidOf(m: Mod) { return isUnc(m) ? SOLID_N : (m.rate >= 80 ? SOLID_G : SOLID_A); }
function borderOf(m: Mod) { return isUnc(m) ? BORDER_N : (m.rate >= 80 ? BORDER_G : BORDER_A); }
function leafLine(m: Mod) { return isUnc(m) ? LINE_N : (m.rate >= 80 ? LINE_G : LINE_A); }

function fmt(n: number) { return n != null ? n.toLocaleString('en-US') : '-'; }
function rateStr(m: Mod) {
  if (!m.total) return '0';
  if (!m.covered) return '未覆盖';
  return Math.round(m.rate) + '%';
}
function fit(s: string, px: number, fs: number) {
  const unit = fs * 0.58;
  const max = Math.max(2, Math.floor(px / unit));
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

/* 两段分割，给无数据段一个最小可见比例 */
function split(values: number[], minFrac = 0.2) {
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const w = values.map(v => Math.max(v, total * minFrac));
  const s = w.reduce((a, b) => a + b, 0) || 1;
  return w.map(x => x / s);
}

/* 画布与内边距 */
const VW = 360, VH = 152;
const OX = 6, OY = 6, OW = 348, OH = 140;
const OP_HEAD = 21, PAD = 6, HOST_HEAD = 18, INSET = 4, GAP = 6;

interface Box {
  key: string; kind: 'container' | 'tile';
  x: number; y: number; w: number; h: number;
  fill: string; stroke: string;
  headerH: number;
  name: string; rate: string; tip: string;
}

const charts = computed(() => {
  const cov = props.coverage || {};
  return METRICS.map((metric) => {
    const host = metricOf(cov.host, metric.hints);
    const kernel = metricOf(cov.kernel, metric.hints);
    const tiling = metricOf(cov.tiling, metric.hints);
    const op = metricOf(cov.op, metric.hints);
    const rest: Mod = {
      covered: Math.max(0, host.covered - tiling.covered),
      total: Math.max(0, host.total - tiling.total),
      rate: 0,
    };
    rest.rate = rest.total ? +(rest.covered / rest.total * 100) : 0;

    // op 内：host / kernel（中间留 GAP）
    const [hF, kF] = split([host.total, kernel.total]);
    const bodyX = OX + PAD, bodyY = OY + OP_HEAD;
    const bodyW = OW - 2 * PAD, bodyH = OH - OP_HEAD - PAD;
    const innerW = Math.max(0, bodyW - GAP);
    const hostW = innerW * hF;
    const kernelX = bodyX + hostW + GAP, kernelW = innerW * kF;

    // host 内：tiling / host·其余（中间留 GAP）
    const hostBodyX = bodyX + INSET, hostBodyY = bodyY + HOST_HEAD;
    const hostBodyW = Math.max(0, hostW - INSET * 2), hostBodyH = bodyH - HOST_HEAD - INSET;
    const [tF, rF] = split([tiling.total, rest.total]);
    const hbInnerW = Math.max(0, hostBodyW - GAP);
    const tilingW = hbInnerW * tF;
    const restX = hostBodyX + tilingW + GAP, restW = hbInnerW * rF;

    const boxes: Box[] = [
      // op 容器
      { key: 'op', kind: 'container', x: OX, y: OY, w: OW, h: OH, fill: tintOf(op), stroke: borderOf(op), headerH: OP_HEAD,
        name: 'op', rate: rateStr(op), tip: `op · ${rateStr(op)} (${fmt(op.covered)}/${fmt(op.total)})` },
      // host 容器
      { key: 'host', kind: 'container', x: bodyX, y: bodyY, w: hostW, h: bodyH, fill: tintOf(host), stroke: borderOf(host), headerH: HOST_HEAD,
        name: 'host', rate: rateStr(host), tip: `host · ${rateStr(host)} (${fmt(host.covered)}/${fmt(host.total)})` },
      // host 内：tiling / 其余
      { key: 'tiling', kind: 'tile', x: hostBodyX, y: hostBodyY, w: tilingW, h: hostBodyH, fill: solidOf(tiling), stroke: leafLine(tiling), headerH: 0,
        name: 'tiling', rate: rateStr(tiling), tip: `tiling · ${rateStr(tiling)} (${fmt(tiling.covered)}/${fmt(tiling.total)}) · 属于 host` },
      { key: 'rest', kind: 'tile', x: restX, y: hostBodyY, w: restW, h: hostBodyH, fill: solidOf(rest), stroke: leafLine(rest), headerH: 0,
        name: 'host·其余', rate: rateStr(rest), tip: `host·其余 · ${rateStr(rest)} (${fmt(rest.covered)}/${fmt(rest.total)})` },
      // kernel（op 内、host 旁）
      { key: 'kernel', kind: 'tile', x: kernelX, y: bodyY, w: kernelW, h: bodyH, fill: solidOf(kernel), stroke: leafLine(kernel), headerH: 0,
        name: 'kernel', rate: rateStr(kernel), tip: `kernel · ${rateStr(kernel)} (${fmt(kernel.covered)}/${fmt(kernel.total)})` },
    ];
    return { label: metric.label, boxes };
  });
});
</script>

<template>
  <div class="mt-row">
    <div v-for="ch in charts" :key="ch.label" class="mt">
      <div class="mt-title">{{ ch.label }}</div>
      <svg class="mt-svg" :viewBox="`0 0 ${VW} ${VH}`">
        <g v-for="b in ch.boxes" :key="b.key">
          <rect :x="b.x" :y="b.y" :width="Math.max(1, b.w)" :height="Math.max(1, b.h)" :fill="b.fill"
            :stroke="b.stroke" :stroke-width="b.kind === 'container' ? 1 : 2" rx="4" />
          <title>{{ b.tip }}</title>
          <!-- 容器标题 -->
          <template v-if="b.kind === 'container'">
            <text :x="b.x + 13" :y="b.y + b.headerH / 2" dominant-baseline="central" font-size="12" :fill="INK" font-weight="700">{{ fit(b.name, b.w - 78, 12) }}</text>
            <text :x="b.x + b.w - 13" :y="b.y + b.headerH / 2" text-anchor="end" dominant-baseline="central" font-size="11" :fill="b.stroke === '#d5dde8' ? SUB : INK" font-weight="700">{{ b.rate }}</text>
          </template>
          <!-- 叶子标签（全部横向） -->
          <template v-else>
            <template v-if="b.w > 72 && b.h > 40">
              <text :x="b.x + 9" :y="b.y + 18" font-size="11" :fill="INK" font-weight="700">{{ fit(b.name, b.w - 18, 11) }}</text>
              <text :x="b.x + 9" :y="b.y + 34" font-size="11" :fill="INK" font-weight="600">{{ b.rate }}</text>
            </template>
            <template v-else-if="b.w > 28 && b.h > 36">
              <text :x="b.x + 5" :y="b.y + 15" font-size="8" :fill="INK" font-weight="700">{{ fit(b.name, b.w - 10, 8) }}</text>
              <text :x="b.x + 5" :y="b.y + 27" font-size="8" :fill="INK" font-weight="600">{{ b.rate }}</text>
            </template>
            <template v-else-if="b.w > 22 && b.h > 18">
              <text :x="b.x + b.w / 2" :y="b.y + b.h / 2 + 4" text-anchor="middle" font-size="10" :fill="INK" font-weight="600">{{ b.rate }}</text>
            </template>
          </template>
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
.mt-row { display: flex; flex-wrap: wrap; justify-content: space-around; align-items: flex-start; gap: 16px; margin: 0 auto; }
.mt { flex: 0 1 430px; max-width: 450; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.mt-title { font-size: 13px; color: var(--wb-muted); font-weight: 600; }
.mt-svg { width: 100%; height: auto; display: block; }
</style>