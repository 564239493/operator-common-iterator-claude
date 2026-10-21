<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{ tree: any; metric?: 'func' | 'line' }>();
const emit = defineEmits<{ select: [payload: { version: string; lib: string; dir: string; op: string }] }>();

const W = 1000, H = 460;
const metric = computed(() => props.metric || 'func');
/* 每个实例唯一前缀，避免同一页面两个树图的 clipPath id 冲突 */
const uid = 'tm' + Math.random().toString(36).slice(2, 8);

/* ===== 布局令牌 ===== */
const OUTER = 2;                                    // 整体外边距（收紧）
const GAP = 18;                                     // 同层兄弟间距（再加大，格子更小）
const PAD: Record<number, number> = { 0: 4, 1: 3, 2: 2, 3: 0 };      // 每层容器内边距（收紧）
const HEADER: Record<number, number> = { 0: 34, 1: 32, 2: 28, 3: 0 }; // 每层容器标题栏高度
const RADIUS: Record<number, number> = { 0: 10, 1: 8, 2: 7, 3: 6 };  // 每层圆角

/* ===== 参考图配色：明亮薄荷绿 / 浅橙 + 深墨字（低饱和、轻盈） ===== */
const GREEN = { tile: '#5ac1b2', tileLine: '#74d0c2', body: '#e5faf4', head: '#abe7dc', pct: '#2b8d8c' };
const AMBER = { tile: '#fec26a', tileLine: '#ffd38a', body: '#fdeed2', head: '#fbdca8', pct: '#a85e19' };
const INK = '#0b2a2c';      // 标题 / 大号百分比 / 名称
const SUB = '#8a9aa5';      // 容器 covered/total 等弱化文字

function isHigh(r: number) { return (Number(r) || 0) >= 80; }
function rateFill(r: number) { return isHigh(r) ? GREEN.tile : AMBER.tile; }
function rateLine(r: number) { return isHigh(r) ? GREEN.tileLine : AMBER.tileLine; }
function rateBody(r: number) { return isHigh(r) ? GREEN.body : AMBER.body; }
function rateHead(r: number) { return isHigh(r) ? GREEN.head : AMBER.head; }
function ratePct(r: number) { return isHigh(r) ? GREEN.pct : AMBER.pct; }
/* 标题带按层级给出深浅，弱化深层、突出顶层分组 */
function headShade(r: number, depth: number) {
  const high = isHigh(r);
  const g = ['#8fdfd0', '#a6e6da', '#c4efe8'];
  const a = ['#f6cf94', '#f9d8a5', '#fcdfb9'];
  return (high ? g : a)[Math.min(depth, 2)];
}
/* 数字格式：容器 1 位小数，叶子取整（对齐参考图） */
function rateFixed1(r: number) { return (Math.round((Number(r) || 0) * 10) / 10).toFixed(1); }
function rateInt(r: number) { return String(Math.round(Number(r) || 0)); }

/* 指标取值 */
function mRate(it: any) { return metric.value === 'line' ? it.line : it.func; }
function mCovered(it: any) { return metric.value === 'line' ? it.line_covered : it.func_covered; }
function mTotal(it: any) { return metric.value === 'line' ? it.line_total : it.func_total; }

function nodeSize(it: any): number {
  if (it.count != null && it.count > 0) return it.count;
  if (it.ops) return it.ops.reduce((s: number, o: any) => s + (o.count || 1), 0);
  if (it.dirs) return it.dirs.reduce((s: number, d: any) => s + nodeSize(d), 0);
  if (it.libraries) return it.libraries.reduce((s: number, l: any) => s + nodeSize(l), 0);
  return 1;
}
function childrenOf(it: any) { return it.ops || it.dirs || it.libraries; }
function labelOf(it: any) {
  if (it.operator) return it.operator;
  if (it.version) return it.version;
  return it.name || '';
}

/* 组聚合：覆盖率 + 汇总 covered/total */
function groupStats(it: any): { rate: number; covered: number; total: number } {
  const leaves: any[] = [];
  (function collect(n: any) {
    if (n.ops) n.ops.forEach((o: any) => leaves.push(o));
    else if (n.dirs) n.dirs.forEach((d: any) => collect(d));
    else if (n.libraries) n.libraries.forEach((l: any) => collect(l));
  })(it);
  if (!leaves.length) return { rate: 0, covered: 0, total: 0 };
  const cov = leaves.reduce((s, o) => s + (metric.value === 'line' ? (o.line_covered || 0) : (o.func_covered || 0)), 0);
  const tot = leaves.reduce((s, o) => s + (metric.value === 'line' ? (o.line_total || 0) : (o.func_total || 0)), 0);
  return { rate: tot ? +(cov / tot * 100).toFixed(1) : 0, covered: cov, total: tot };
}

function fmt(n: number) { return n != null ? n.toLocaleString('en-US') : '-'; }
/* 按可用像素截断文字：中文≈1em、其余≈0.56em；超长一律省略号显示 */
function fit(s: string, px: number, fontSize: number) {
  let out = '';
  let used = 0;
  for (const ch of s) {
    const cw = /[\u2e80-\u9fff\uff00-\uffef]/.test(ch) ? fontSize : fontSize * 0.56;
    if (used + cw > px) break;
    out += ch;
    used += cw;
  }
  return out === s ? s : (out.length ? out.slice(0, -1) + '…' : '…');
}

interface Rect { x: number; y: number; w: number; h: number; node: any; depth: number; leaf: boolean; ctx: any; rate: number; covered: number; total: number; headerH: number; pad: number }

/*
 * squarified treemap：把一组子节点铺成尽量方正的矩形（而不是一条条长条），
 * 使多个算子能「一行多个」排布、文字有足够空间不重叠，也更省留白。
 */
function squarify(items: any[], x0: number, y0: number, w: number, h: number) {
  const nodes = items.map(it => ({ it, area: nodeSize(it) })).sort((a, b) => b.area - a.area);
  const total = nodes.reduce((s, n) => s + n.area, 0) || 1;
  const k = (w * h) / total;
  nodes.forEach(n => { n.area *= k; });
  const out: { it: any; x: number; y: number; w: number; h: number }[] = [];
  let ax = x0, ay = y0, bx = x0 + w, by = y0 + h;
  let value = w * h;
  let i0 = 0, i1 = 0;
  const n = nodes.length;
  while (i0 < n) {
    const dx = bx - ax, dy = by - ay;
    if (dx <= 0 || dy <= 0) {
      for (; i0 < n; i0++) out.push({ it: nodes[i0].it, x: ax, y: ay, w: 0, h: 0 });
      break;
    }
    let sum = nodes[i1].area; i1++;
    let minV = sum, maxV = sum;
    const alpha = Math.max(dy / dx, dx / dy) / value;
    let beta = sum * sum * alpha;
    let minRatio = Math.max(maxV / beta, beta / minV);
    for (; i1 < n; ++i1) {
      const v = nodes[i1].area;
      sum += v;
      if (v < minV) minV = v;
      if (v > maxV) maxV = v;
      beta = sum * sum * alpha;
      const r = Math.max(maxV / beta, beta / minV);
      if (r > minRatio) { sum -= v; break; }
      minRatio = r;
    }
    const row = nodes.slice(i0, i1);
    if (dx < dy) {
      const bh = dy * sum / value, kk = dx / sum;
      let cx = ax;
      for (const c of row) { out.push({ it: c.it, x: cx, y: ay, w: c.area * kk, h: bh }); cx += c.area * kk; }
      ay += bh;
    } else {
      const bw = dx * sum / value, kk = dy / sum;
      let cy = ay;
      for (const c of row) { out.push({ it: c.it, x: ax, y: cy, w: bw, h: c.area * kk }); cy += c.area * kk; }
      ax += bw;
    }
    value -= sum;
    i0 = i1;
  }
  return out;
}

function layout(items: any[], x: number, y: number, w: number, h: number, depth: number, ctx: any, out: Rect[]) {
  if (!items || !items.length || w <= 0 || h <= 0) return;
  const cells = squarify(items, x, y, w, h);
  // 统一内缩，让每个格子都小一圈（避免少数算子时格子撑满）
  const inset = GAP / 2;
  for (const cell of cells) {
    const it = cell.it;
    const leaf = it.count != null;
    const rect = {
      x: cell.x + inset,
      y: cell.y + inset,
      w: Math.max(1, cell.w - inset * 2),
      h: Math.max(1, cell.h - inset * 2),
    };
    const childCtx = { ...ctx };
    if (depth === 0) childCtx.version = it.version;
    if (depth === 1) childCtx.lib = it.name;
    if (depth === 2) childCtx.dir = it.name;

    const pad = leaf ? 0 : PAD[depth];
    const headerH = (!leaf && rect.w > 100 && rect.h > 52) ? HEADER[depth] : 0;
    const topGap = headerH > 0 ? headerH + 2 : pad;
    const stats = leaf
      ? { rate: mRate(it), covered: mCovered(it) || 0, total: mTotal(it) || 0 }
      : groupStats(it);

    out.push({
      x: rect.x, y: rect.y, w: rect.w, h: rect.h,
      node: it, depth, leaf, ctx: childCtx,
      rate: stats.rate, covered: stats.covered, total: stats.total,
      headerH, pad,
    });

    const ch = childrenOf(it);
    if (ch && ch.length) {
      layout(ch, rect.x + pad, rect.y + topGap, rect.w - pad * 2, rect.h - topGap - pad, depth + 1, childCtx, out);
    }
  }
}

const rects = computed<Rect[]>(() => {
  const out: Rect[] = [];
  if (props.tree && props.tree.versions && props.tree.versions.length) {
    layout(props.tree.versions, OUTER, OUTER, W - OUTER * 2, H - OUTER * 2, 0, {}, out);
  }
  return out;
});
const leafRects = computed(() => rects.value.filter(r => r.leaf));
const midRects = computed(() => rects.value.filter(r => !r.leaf));

/* 整体汇总（头部用） */
const overall = computed(() => {
  const leaves: any[] = [];
  (function collect(nodes: any[]) {
    nodes.forEach((n: any) => {
      if (n.ops) n.ops.forEach((o: any) => leaves.push(o));
      else if (n.dirs) collect(n.dirs);
      else if (n.libraries) collect(n.libraries);
    });
  })(props.tree ? props.tree.versions || [] : []);
  const mc = leaves.reduce((s, o) => s + (metric.value === 'line' ? (o.line_covered || 0) : (o.func_covered || 0)), 0);
  const mt = leaves.reduce((s, o) => s + (metric.value === 'line' ? (o.line_total || 0) : (o.func_total || 0)), 0);
  return { rate: mt ? +(mc / mt * 100).toFixed(1) : 0, covered: mc, total: mt };
});

function leafLines(r: Rect) {
  return r.node.line_total != null ? r.node.line_total : (r.node.count || 0);
}

/* 标题字号随层级递减 */
function headerNameFont(depth: number) { return depth === 0 ? 20 : depth === 1 ? 18 : 16; }
function headerRateFont(depth: number) { return depth === 0 ? 20 : depth === 1 ? 18 : 16; }
function headerTotalFont(depth: number) { return depth === 0 ? 16 : depth === 1 ? 15 : 13; }

/* 容器标题带：仅上方圆角，下方直边 */
function headBand(r: Rect): string {
  const rad = Math.min(RADIUS[r.depth], r.headerH / 2, r.w / 2);
  const x = r.x, y = r.y, w = r.w, h = r.headerH;
  return `M${x + rad},${y} L${x + w - rad},${y} Q${x + w},${y} ${x + w},${y + rad} L${x + w},${y + h} L${x},${y + h} L${x},${y + rad} Q${x},${y} ${x + rad},${y} Z`;
}

function onSelect(r: Rect) {
  emit('select', { version: r.ctx.version || '', lib: r.ctx.lib || '', dir: r.ctx.dir || '', op: r.node.dir_name });
}
</script>

<template>
  <div class="tm-panel">
    <!-- 面板头：指标名 + 整体汇总 -->
    <div class="tm-head">
      <span class="tm-name">{{ metric === 'line' ? '行覆盖率' : '函数覆盖率' }}</span>
      <span class="tm-sum">
        <b :style="{ color: ratePct(overall.rate) }">{{ overall.rate }}%</b>
        <em>{{ overall.covered }} / {{ overall.total }}</em>
      </span>
    </div>

    <svg class="treemap" viewBox="0 0 1000 460" preserveAspectRatio="xMidYMid meet">
      <!-- 容器：浅色底 + 略深标题带 + 名称/百分比/covered-total -->
      <g v-for="(r, i) in midRects" :key="'m' + i">
        <rect :x="r.x" :y="r.y" :width="Math.max(1, r.w)" :height="Math.max(1, r.h)"
          :fill="rateBody(r.rate)" :stroke="rateHead(r.rate)" stroke-width="1" :rx="RADIUS[r.depth]" />
        <path v-if="r.headerH > 0" :d="headBand(r)" :fill="headShade(r.rate, r.depth)" />
        <title>{{ labelOf(r.node) }} · {{ r.rate }}% ({{ r.covered }}/{{ r.total }})</title>
        <template v-if="r.headerH > 0">
          <defs>
            <clipPath :id="uid + '-mc' + i">
              <rect :x="r.x" :y="r.y" :width="Math.max(1, r.w)" :height="Math.max(1, r.headerH)" />
            </clipPath>
          </defs>
          <g :clip-path="'url(#' + uid + '-mc' + i + ')'">
            <text :x="r.x + 14" :y="r.y + r.headerH / 2" dominant-baseline="central"
              :font-size="headerNameFont(r.depth)" :fill="INK" font-weight="700">{{ fit(labelOf(r.node), r.w - (r.w > 340 ? 250 : r.w > 240 ? 112 : 20), headerNameFont(r.depth)) }}</text>
            <text v-if="r.w > 240" :x="r.x + r.w - (r.w > 340 ? 112 : 14)" :y="r.y + r.headerH / 2" text-anchor="end" dominant-baseline="central"
              :font-size="headerRateFont(r.depth)" :fill="ratePct(r.rate)" font-weight="700">{{ rateFixed1(r.rate) }}%</text>
            <text v-if="r.w > 340" :x="r.x + r.w - 14" :y="r.y + r.headerH / 2" text-anchor="end" dominant-baseline="central"
              :font-size="headerTotalFont(r.depth)" :fill="SUB">{{ r.covered }} / {{ r.total }}</text>
          </g>
        </template>
      </g>
      <!-- 叶子：算子实心色块，深墨字（按格子大小分级，并裁剪防溢出） -->
      <g v-for="(r, i) in leafRects" :key="'l' + i" class="leaf" @click="onSelect(r)">
        <rect :x="r.x" :y="r.y" :width="Math.max(1, r.w)" :height="Math.max(1, r.h)"
          :fill="rateFill(r.rate)" :stroke="rateLine(r.rate)" stroke-width="1.5" :rx="RADIUS[r.depth]" />
        <title>{{ r.node.operator }} · {{ r.rate }}% ({{ r.covered }}/{{ r.total }})</title>
        <defs>
          <clipPath :id="uid + '-lc' + i">
            <rect :x="r.x" :y="r.y" :width="Math.max(1, r.w)" :height="Math.max(1, r.h)" :rx="RADIUS[r.depth]" />
          </clipPath>
        </defs>
        <g :clip-path="'url(#' + uid + '-lc' + i + ')'">
          <!-- 大格子：名称 + 大号% + covered/total + 底部行数 -->
          <template v-if="r.w > 190 && r.h > 118">
            <text :x="r.x + 18" :y="r.y + 26" font-size="18" :fill="INK" font-weight="700">{{ fit(r.node.operator, r.w - 36, 18) }}</text>
            <text :x="r.x + 18" :y="r.y + 68" font-size="38" :fill="INK" font-weight="750">{{ rateInt(r.rate) }}%</text>
            <text :x="r.x + 18" :y="r.y + 92" font-size="16" :fill="INK" fill-opacity="0.62">{{ r.covered }} / {{ r.total }}</text>
            <text :x="r.x + 18" :y="r.y + r.h - 14" font-size="15" :fill="INK" fill-opacity="0.62">{{ fmt(leafLines(r)) }} 行</text>
          </template>
          <!-- 中格子：名称 + 中号%（够高再加行数） -->
          <template v-else-if="r.w > 100 && r.h > 70">
            <text :x="r.x + 13" :y="r.y + 22" font-size="15" :fill="INK" font-weight="700">{{ fit(r.node.operator, r.w - 26, 15) }}</text>
            <text :x="r.x + 13" :y="r.y + 52" font-size="26" :fill="INK" font-weight="750">{{ rateInt(r.rate) }}%</text>
            <text v-if="r.h > 100" :x="r.x + 13" :y="r.y + r.h - 12" font-size="13" :fill="INK" fill-opacity="0.6">{{ fmt(leafLines(r)) }} 行</text>
          </template>
          <!-- 小格子：仅 % -->
          <template v-else-if="r.w > 50 && r.h > 30">
            <text :x="r.x + r.w / 2" :y="r.y + r.h / 2 + 5" text-anchor="middle" font-size="15" :fill="INK" font-weight="700">{{ rateInt(r.rate) }}%</text>
          </template>
        </g>
      </g>
    </svg>

    <!-- 底部图例 -->
    <div class="tm-foot">
      <span>面积: {{ metric === 'line' ? '总行数' : '函数数' }} · 颜色: 对应覆盖率</span>
      <span class="tm-foot-right">
        <i class="dot" :style="{ background: GREEN.tile }"></i>≥ 80%
        <i class="dot" :style="{ background: AMBER.tile }"></i>&lt; 80%
      </span>
    </div>
  </div>
</template>

<style scoped>
.tm-panel { background: var(--wb-card); border: 1px solid var(--wb-line); border-radius: 12px; padding: 8px 10px; box-shadow: var(--wb-shadow); }
.tm-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
.tm-name { font-size: 15px; font-weight: 650; color: var(--wb-ink); }
.tm-sum { font-size: 13px; color: var(--wb-muted); display: inline-flex; align-items: baseline; gap: 8px; }
.tm-sum b { font-size: 18px; font-weight: 750; font-family: "JetBrains Mono", Consolas, monospace; }
.tm-sum em { font-style: normal; color: var(--wb-faint); }
.treemap { width: 100%; height: auto; aspect-ratio: 1000 / 460; display: block; }
.leaf { cursor: pointer; }
.leaf rect { transition: filter .12s, stroke-width .12s; }
.leaf:hover rect { filter: brightness(1.04); stroke-width: 3; }
.tm-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 6px; font-size: 12px; color: var(--wb-muted); }
.tm-foot-right { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 11px; height: 11px; border-radius: 3px; display: inline-block; margin-left: 6px; }
</style>