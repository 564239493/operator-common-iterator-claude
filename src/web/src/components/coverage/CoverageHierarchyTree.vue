<template>
  <div class="ht-panel">
    <!-- 面板头：三项整体覆盖率（与分层矩形图一致） + 子视图切换 -->
    <div class="ht-head">
      <span class="ht-name">覆盖率总览</span>
      <span class="ht-right">
        <span class="ht-mode">
          <el-switch
            v-model="subMode"
            size="small"
            active-value="indent"
            active-text="缩进树列表"
            inactive-value="node"
            inactive-text="树形图"
          />
        </span>
        <span class="ht-sum">
          <template v-for="m in METRICS" :key="m.key">
            <i class="dot" :style="{ background: m.color }"></i>
            <span class="ht-sum-item">
              <b :style="{ color: m.color }">{{ overall[m.key].rate }}%</b>
              <em>{{ overall[m.key].covered }} / {{ overall[m.key].total }}</em>
            </span>
          </template>
        </span>
      </span>
    </div>

    <!-- 树形图 / 缩进树列表 切换区：节点样式与分层矩形图矩形一致（蓝底/蓝线/圆角 + 三指标） -->
    <div class="ht-scroll">
      <svg v-if="subMode === 'node'" class="ht-svg" :width="items.W" :height="items.H" :viewBox="`0 0 ${items.W} ${items.H}`" role="img" aria-label="覆盖率层级树">
        <!-- 虚线折线：父右缘 → 间隙中点（垂直）→ 子卡片左边缘 -->
        <g>
          <path v-for="(l, i) in items.links" :key="'l' + i" :d="`M ${l.x1} ${l.y1} H ${l.xm} V ${l.y2} H ${l.x3}`"
            fill="none" class="ht-link" />
        </g>
        <g v-for="it in items.nodes" :key="it.key">
          <g class="ht-node" @click="pick(it)">
            <rect class="card" :x="it.cx - NW / 2" :y="it.cy - NH / 2" :width="NW" :height="NH" rx="10"
              fill="var(--wb-blue-soft)" stroke="var(--wb-blue-line)" stroke-width="1" />
            <text class="nm" :x="it.cx - NW / 2 + 12" :y="it.cy - NH / 2 + 22" font-size="12.5" font-weight="650">{{ it.name }}</text>
            <rect class="tag" :x="it.cx + NW / 2 - 44" :y="it.cy - NH / 2 + 10" width="34" height="16" rx="4" />
            <text class="tag-t" :x="it.cx + NW / 2 - 27" :y="it.cy - NH / 2 + 22" font-size="10" text-anchor="middle">{{ it.tag }}</text>
            <g v-for="(m, mi) in it.m" :key="mi">
              <text class="ml" :x="it.cx - NW / 2 + 12" :y="it.cy - NH / 2 + 53 + mi * 18" font-size="10.5">{{ m.label }}</text>
              <rect class="mtrack" :x="it.cx - NW / 2 + 44" :y="it.cy - NH / 2 + 44 + mi * 18" :width="TRACK_W" height="8" rx="4" />
              <rect class="mfill" :x="it.cx - NW / 2 + 44" :y="it.cy - NH / 2 + 44 + mi * 18" :width="m.fillW" height="8" rx="4" :fill="m.color" />
              <text class="mp" :x="it.cx + NW / 2 - 10" :y="it.cy - NH / 2 + 53 + mi * 18" font-size="10.5" font-weight="700"
                text-anchor="end" :fill="m.color">{{ m.txt }}</text>
            </g>
            <title>{{ it.tip }}</title>
          </g>
          <g v-if="it.hasKids" class="ht-toggle" @click.stop="toggle(it)">
            <circle :cx="it.cx + NW / 2" :cy="it.cy" r="7" fill="var(--wb-card)" stroke="var(--wb-blue)" stroke-width="1.2" />
            <text :x="it.cx + NW / 2" :y="it.cy + 3.5" font-size="10" font-weight="700" text-anchor="middle"
              fill="var(--wb-blue)">{{ it.collapsed ? '+' : '−' }}</text>
          </g>
        </g>
      </svg>

      <!-- 方案 A · 缩进树列表 -->
      <div v-else class="it-wrap">
        <ul class="it-tree">
          <li v-for="v in indentTree" :key="v.key" class="it-root-li">
            <CoverageIndentNode :node="v" @select="pick" @toggle="toggle" />
          </li>
        </ul>
      </div>
    </div>

    <!-- 底部：说明 + 图例（指标 + 层级） -->
    <div class="ht-foot">
      <span>点击节点下钻 · 点「− / +」折叠或展开 · 悬停查看函数、分支和行的明细</span>
      <span class="ht-legend">
        <span class="lg">
          <i class="dot" :style="{ background: 'var(--wb-green)' }"></i>函数
          <i class="dot" :style="{ background: 'var(--wb-blue)' }"></i>分支
          <i class="dot" :style="{ background: 'var(--wb-orange)' }"></i>行
        </span>
        <span class="lg">
          <i class="dot" :style="{ background: 'var(--wb-purple)' }"></i>版本
          <i class="dot" :style="{ background: 'var(--wb-blue)' }"></i>算子库
          <i class="dot" :style="{ background: 'var(--wb-green)' }"></i>域
          <i class="dot" :style="{ background: 'var(--wb-orange)' }"></i>算子目录
          <i class="dot" :style="{ background: 'var(--wb-red)' }"></i>算子
          <i class="dot" :style="{ background: '#c26bd6' }"></i>产品
        </span>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue';
import CoverageIndentNode from './CoverageIndentNode.vue';

const props = defineProps<{ tree: { versions: any[] } }>();
const emit = defineEmits<{ (e: 'select', p: { path: string[] }): void }>();

/* 子视图切换：node = 节点连线树（方案 C）/ indent = 缩进树列表（方案 A） */
const subMode = ref<'node' | 'indent'>('node');

/* 三个指标（与分层矩形图完全一致：函数绿 / 分支蓝 / 行橙） */
const METRICS = [
  { key: 'func', label: '函数', color: 'var(--wb-green)' },
  { key: 'branch', label: '分支', color: 'var(--wb-blue)' },
  { key: 'line', label: '行', color: 'var(--wb-orange)' },
] as const;

/* 布局常量：PADX ≥ NW/2（根节点左缘不被裁切）；Y0 ≥ NH/2（首行卡片不被上边界截断）；
 * ROWH ≥ NH（避免相邻行重叠） */
const PADX = 116, COLW = 300, NW = 208, NH = 102, ROWH = 124, Y0 = 56;
const TRACK_W = 120;

function rateOf(n: any, k: 'func' | 'branch' | 'line') {
  const s = (n && n.summary) || {};
  const tot = s[`${k}_total`] || 0, cov = s[`${k}_covered`] || 0;
  return tot ? Math.round((cov / tot) * 1000) / 10 : 0;
}
function nmOf(n: any) {
  /* 显示目录名（name=目录名；operator 仅为详情元数据，产品目录/单算子目录若优先
   * operator 会显示成算子名，丢掉 A2 等产品身份） */
  const s = (n.name || n.operator || '').toString();
  return s.length > 18 ? s.slice(0, 17) + '…' : s;
}
/* 三指标行（缩进树与树形图共用） */
function mRows(n: any) {
  return METRICS.map(m => {
    const r = rateOf(n, m.key);
    const s = (n && n.summary) || {};
    const tot = s[`${m.key}_total`] || 0;
    return {
      label: m.label, color: m.color,
      fillW: tot ? Math.max(2, Math.round(TRACK_W * r / 100)) : 0,
      txt: tot ? Math.round(r) + '%' : '—',
      pct: tot ? Math.round(r) : 0,
    };
  });
}
/* 缩进树的层级标签（非叶子按深度：版本/算子库/域/算子目录/算子；叶子第 6 层=产品，其余叶子=算子） */
function indentTag(n: any, depth: number, kidCount: number) {
  if (kidCount) return (['版本', '算子库', '域', '算子目录', '算子', '产品'][Math.min(depth, 5)] || '算子');
  return depth >= 5 ? '产品' : '算子';
}
/* 每层一个颜色（缩进树层级标识着色：版本/算子库/域/算子目录/算子/产品） */
const DEPTH_COLORS = [
  'var(--wb-purple)', 'var(--wb-blue)', 'var(--wb-green)', 'var(--wb-orange)',
  'var(--wb-red)', '#c26bd6',
];

/* 折叠态：按节点路径（/ 连接）记录 */
const collapsed = reactive(new Set<string>());
function toggle(it: any) {
  if (collapsed.has(it.key)) collapsed.delete(it.key); else collapsed.add(it.key);
}
function pick(it: any) { emit('select', { path: it.path }); }

/* 整体汇总（面板头）：三个指标跨版本累加 */
const overall = computed(() => {
  const versions = (props.tree && props.tree.versions) || [];
  const sum = (k: 'func' | 'branch' | 'line') => {
    const covered = versions.reduce((s, v) => s + ((v.summary && v.summary[`${k}_covered`]) || 0), 0);
    const total = versions.reduce((s, v) => s + ((v.summary && v.summary[`${k}_total`]) || 0), 0);
    return { rate: total ? +(covered / total * 100).toFixed(1) : 0, covered, total };
  };
  return { func: sum('func'), branch: sum('branch'), line: sum('line') };
});

/* 布局：深度优先后序遍历，叶子顺序决定 y；父节点居中于其叶子区间 */
const items = computed(() => {
  const nodes: any[] = [], links: any[] = [];
  let leaf = 0, maxDepth = 0;
  function tipOf(n: any) {
    const s = (n && n.summary) || {};
    const part = (k: 'func' | 'branch' | 'line', lab: string) =>
      `${lab} ${s[`${k}_total`] ? Math.round(rateOf(n, k)) + '%' : '—'} (${s[`${k}_covered`] || 0}/${s[`${k}_total`] || 0})`;
    // 算子节点的产品覆盖率概览（第 6 层产品目录），体现产品概念
    const kids = Array.isArray(n.children) ? n.children : [];
    const prods = kids.filter((k: any) => !(k.children && k.children.length));
    const prodTip = prods.length
      ? ' · 产品：' + prods.map((k: any) => {
          const ks = (k && k.summary) || {};
          return `${k.name} ${ks.func_total ? Math.round(rateOf(k, 'func')) + '%' : '—'}`;
        }).join('  ')
      : '';
    return `${n.name || n.operator || ''} · ${part('func', '函数')} · ${part('branch', '分支')} · ${part('line', '行')} · ${(n.children && n.children.length) ? '目录' : '点击下钻'}${prodTip}`;
  }
  function walk(n: any, path: string[], depth: number): [number, number] {
    maxDepth = Math.max(maxDepth, depth);
    const key = path.join('/');
    const kids = Array.isArray(n.children) ? n.children : [];
    const hasKids = kids.length > 0;
    const cx = PADX + depth * COLW;
    if (!hasKids || collapsed.has(key)) {
      const lo = leaf, hi = leaf; leaf += 1;
      nodes.push({ n, path, depth, key, hasKids, collapsed: collapsed.has(key), cx,
        cy: (lo + hi) / 2 * ROWH + Y0,
        name: nmOf(n), tag: indentTag(n, depth, kids.length), m: mRows(n), tip: tipOf(n) });
      return [lo, hi];
    }
    const ranges = kids.map((k: any) => walk(k, [...path, k.name], depth + 1));
    const lo = Math.min(...ranges.map(r => r[0])), hi = Math.max(...ranges.map(r => r[1]));
    const cy = (lo + hi) / 2 * ROWH + Y0;
    nodes.push({ n, path, depth, key, hasKids, collapsed: false, cx, cy,
      name: nmOf(n), tag: indentTag(n, depth, kids.length), m: mRows(n), tip: tipOf(n) });
    ranges.forEach((r, i) => {
      // 三段折线：父右缘 → 列间隙中点 → 垂直 → 子卡片左边缘（不穿过卡片）
      links.push({
        x1: cx + NW / 2, y1: cy,
        xm: cx + NW / 2 + (COLW - NW) / 2,
        x3: cx + COLW - NW / 2, y2: (r[0] + r[1]) / 2 * ROWH + Y0,
      });
    });
    return [lo, hi];
  }
  (props.tree.versions || []).forEach((v: any) => { walk(v, [v.name], 0); leaf += 1; }); // 版本间留空隙
  return {
    nodes, links,
    W: (maxDepth + 1) * COLW + PADX * 2,
    // 高度只覆盖内容行 + 底部留白：Y0 + 最后内容行偏移 + 卡片半高 + 24
    H: Y0 + Math.max(0, leaf - 2) * ROWH + NH / 2 + 24,
  };
});

/* 方案 A · 缩进树列表：递归构建（与树形图共用折叠态 collapsed） */
const indentTree = computed(() => {
  function build(n: any, path: string[], depth: number): any {
    const kids = Array.isArray(n.children) ? n.children : [];
    return {
      n, path, key: path.join('/'), name: nmOf(n),
      tag: indentTag(n, depth, kids.length),
      color: DEPTH_COLORS[Math.min(depth, 5)],
      // 指标顺序对齐 cov-node-metrics：函数 / 行 / 分支
      m: [0, 2, 1].map(i => mRows(n)[i]),
      hasKids: kids.length > 0,
      collapsed: collapsed.has(path.join('/')),
      children: kids.map((k: any) => build(k, [...path, k.name], depth + 1)),
    };
  }
  return (props.tree.versions || []).map((v: any) => build(v, [v.name], 0));
});
</script>

<style scoped>
/* 面板容器与分层矩形图 lm-panel 同款 */
.ht-panel {
  background: var(--wb-card);
  border: 1px solid var(--wb-line);
  border-radius: 12px;
  padding: 10px 12px 8px;
  box-shadow: var(--wb-shadow);
  height: 100%;
  max-height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-size: 14px;
}
.ht-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; flex-wrap: wrap; gap: 6px; }
.ht-name { font-size: 15px; font-weight: 650; color: var(--wb-ink); }
.ht-right { display: inline-flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.ht-mode { display: inline-flex; align-items: center; }
.ht-sum { display: inline-flex; align-items: baseline; gap: 12px; font-size: 13px; color: var(--wb-muted); }
.ht-sum-item { display: inline-flex; align-items: baseline; gap: 6px; }
.ht-sum b { font-size: 16px; font-weight: 750; font-family: "JetBrains Mono", Consolas, monospace; }
.ht-sum em { font-style: normal; color: var(--wb-faint); font-size: 12px; }

.ht-scroll { flex: 1 1 0; min-height: 0; overflow: auto; }
.ht-svg { display: block; }

/* 虚线肘线：统一蓝色调 */
.ht-link { stroke: var(--wb-blue-line); stroke-width: 1.6; stroke-dasharray: 6 4; }

/* 节点：蓝底/蓝线/圆角（与分层矩形图矩形一致）；hover 高亮边框 */
.ht-node { cursor: pointer; }
.ht-node:hover .card { stroke: var(--wb-blue); }
.ht-node .nm { fill: var(--wb-ink); }
.ht-node .tag { fill: var(--wb-line-soft); }
.ht-node .tag-t { fill: var(--wb-muted); }
.ht-node .ml { fill: var(--wb-muted); }
.ht-node .mtrack { fill: var(--wb-line-soft); stroke: var(--wb-blue-line); }
.ht-toggle { cursor: pointer; }

.ht-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; font-size: 12px; color: var(--wb-muted); flex-wrap: wrap; gap: 6px; }
.ht-legend { display: inline-flex; align-items: center; gap: 14px; }
.lg { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }

/* 方案 A · 缩进树列表容器 */
.it-wrap { padding: 4px 8px; }
.it-tree { list-style: none; margin: 0; padding: 0; }
.it-root-li { list-style: none; }
</style>