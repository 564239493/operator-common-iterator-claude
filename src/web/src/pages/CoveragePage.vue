<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import TaskPicker from '../components/task/TaskPicker.vue';
import TopBar from '../components/shell/TopBar.vue';
import CoverageList from '../components/coverage/CoverageList.vue';
import ModuleTreemap from '../components/coverage/ModuleTreemap.vue';
import CoverageTreemap from '../components/coverage/CoverageTreemap.vue';

// 数据源：全局最终报告（ops_final_cov_report）默认；任务内嵌（runs/*/ops_cov_report）切换
const source = ref<'global' | 'run'>('global');

/* ===== 数据树（递归：版本/算子库/域/目录/算子，每节点带 summary + children） ===== */
const tree = ref<{ versions: any[] }>({ versions: [] });
const loading = ref(true);
// path：节点名称列表（变长）。[] = 整体统计；[版本] / [版本,库] / … / […叶子名]
const path = ref<string[]>([]);

const detailLoading = ref(false);
const coverData = ref<any>(null);
const uncoveredReasons = ref<Record<string, any>>({});
const coveredUncoveredLines = ref<Record<string, any>>({});
const expandedFunc = ref('');

// 任务内嵌：任务名称列表 + 当前选中的任务
const runList = ref<any[]>([]);
const runSel = ref('');

// 全局视图：列表下钻 / 矩阵视图（树图）
const viewMode = ref<'list' | 'treemap'>('treemap');

const treeUrl = () => '/api/review/cover/tree';
const detailUrl = (nodePath: string[], kind: string) =>
  '/api/review/cover/' + nodePath.map(encodeURIComponent).join('/') + '/' + kind;
const runsUrl = () => '/api/review/cover/runs';
const runDetailUrl = (runId: string, kind: string) => '/api/review/runs/' + encodeURIComponent(runId) + '/cover/' + kind;

function switchSource(s: 'global' | 'run') {
  source.value = s;
  path.value = [];
  runSel.value = '';
  coverData.value = null;
  if (s === 'global') loadTree();
  else loadRunList();
}

/* ===== 节点辅助（递归树，统一 children 形状） ===== */
const childrenOf = (n: any) => (n && Array.isArray(n.children) ? n.children : []);

// 按名称序列在递归树中定位节点（path 末位 = 当前选中节点）
function findNode(namePath: string[]): any {
  if (!namePath.length) return null;
  let node: any = tree.value.versions.find((v: any) => v.name === namePath[0]) || null;
  for (let i = 1; i < namePath.length && node; i++) {
    node = childrenOf(node).find((c: any) => c.name === namePath[i]) || null;
  }
  return node;
}
// 当前选中节点（path 末位）；path=[] 时为 null
const current = computed(() => (path.value.length ? findNode(path.value) : null));
// 当前节点是否带 coverage json（merged/leaf）→ 展示覆盖率 + 未覆盖详情
const hasCoverage = (n: any) => !!n && (n.kind === 'merged' || n.kind === 'leaf');
// 当前节点的子目录（供下拉切换）；path=[] 时为版本列表
const currentChildren = computed(() => {
  if (!path.value.length) return tree.value.versions;
  const node = findNode(path.value);
  return node ? childrenOf(node) : [];
});
// 展示模式：detail（本目录覆盖率+未覆盖详情）/ children（子目录卡片列表）
// 有 coverage json 的节点默认 detail；否则（agg 汇总层）只能 children
const mode = ref<'detail' | 'children'>('children');
function ensureMode(n: any) {
  if (!n) { mode.value = 'children'; return; }
  if (hasCoverage(n)) mode.value = 'detail';
  else mode.value = 'children';
}

const detailTitle = computed(() => {
  if (source.value === 'run') return coverData.value ? coverData.value.operator : runSel.value;
  const c = current.value;
  return c ? (hasCoverage(c) ? (c.operator || c.name) : c.name) : '';
});
const detailMeta = computed(() => {
  if (source.value === 'run') return runSel.value;
  return path.value.join(' / ');
});
// 下拉切换选项：当前节点各子目录名（稳定不随本目录详情/子列表切换而消失），选中即跳到该子目录
const subOptions = computed(() => currentChildren.value.map((c: any) => ({
  value: c.name,
  label: c.name,
})));
const showDropdown = computed(() => source.value === 'global' && path.value.length > 0 && subOptions.value.length > 0);
// el-select 的受控值：用独立 ref 而非固定空串，避免选择后下拉无法再次展开
const subSel = ref('');
const showDetail = computed(() => (source.value === 'global' && mode.value === 'detail' && hasCoverage(current.value)) || (source.value === 'run' && !!runSel.value));

/* ===== 聚合与统计（读节点 summary，不再从叶子平均） ===== */
const _sum = (nodes: any[], key: string) => nodes.reduce((s, n) => s + ((n && n.summary && n.summary[key]) || 0), 0);

function countLeaves(n: any): number {
  if (!n) return 0;
  if (!childrenOf(n).length) return 1;
  return childrenOf(n).reduce((s, c) => s + countLeaves(c), 0);
}
function countNodes(n: any): number {
  if (!n) return 0;
  return 1 + childrenOf(n).reduce((s, c) => s + countNodes(c), 0);
}

const stats = computed(() => {
  const versions = tree.value.versions;
  const leaves = versions.flatMap((v: any) => {
    const out: any[] = [];
    (function walk(n: any) { if (!childrenOf(n).length) out.push(n); childrenOf(n).forEach(walk); })(v);
    return out;
  });
  const f = { c: _sum(versions, 'func_covered'), t: _sum(versions, 'func_total') };
  const l = { c: _sum(versions, 'line_covered'), t: _sum(versions, 'line_total') };
  const b = { c: _sum(versions, 'branch_covered'), t: _sum(versions, 'branch_total') };
  return {
    ver: versions.length,
    libs: versions.reduce((s, v) => s + childrenOf(v).length, 0),
    leaves: leaves.length,
    func: f.t ? +(f.c / f.t * 100).toFixed(1) : 0,
    line: l.t ? +(l.c / l.t * 100).toFixed(1) : 0,
    branch: b.t ? +(b.c / b.t * 100).toFixed(1) : 0,
  };
});

const kpiItems = computed(() => {
  const s = stats.value;
  return [
    { n: s.ver, u: '个', l: '版本', color: '' },
    { n: s.libs, u: '个', l: '算子库', color: '' },
    { n: s.leaves, u: '个', l: '算子报告', color: '' },
    { n: s.func, u: '%', l: '整体函数覆盖率', color: rateHex(s.func) },
    { n: s.line, u: '%', l: '整体行覆盖率', color: rateHex(s.line) },
    { n: s.branch, u: '%', l: '整体分支覆盖率', color: rateHex(s.branch) },
  ];
});

/* 当前节点的子级卡片（统一归一化，供 CoverageList 渲染）
 * - 有子目录的节点（agg 汇总 / merged 目录）：标题=目录名，点击继续下钻
 * - 无子目录的叶子（真实算子）：标题=算子名，点击进详情
 */
const childCards = computed(() => currentChildren.value.map((n: any) => {
  const s = n.summary || {};
  const kids = childrenOf(n);
  const cov = hasCoverage(n);
  const title = cov ? (n.operator || n.name) : n.name;
  const badge = cov
    ? (n.has_analysis ? '含分析' : '算子')
    : (n.kind === 'merged' ? '目录·合并' : '汇总');
  const meta = kids.length
    ? `${kids.length} 个子项 · ${countLeaves(n)} 个算子`
    : (n.domain || '-');
  const sub = kids.length ? kids.map((c: any) => (c.operator || c.name)).join('、') : (cov ? (n.domain || '') : '');
  return { key: n.name, title, badge, badgeType: 'has-md', meta,
    func: s.func || 0, line: s.line || 0, branch: s.branch || 0, sub: sub || undefined };
}));
const runCards = computed(() => runList.value.map((r: any) => ({
  key: r.run_id, title: r.run_id, badge: r.has_analysis ? '含分析' : '内嵌', badgeType: 'has-md',
  meta: `${r.operator} · ${r.domain || '-'}`, func: r.func, line: r.line, branch: r.branch,
})));

function rateHex(r: number) { return r >= 80 ? '#159b78' : r >= 50 ? '#df8a27' : '#da5961'; }
function rateCls(r: number) { return r >= 80 ? 'ok' : r >= 50 ? 'mid' : 'bad'; }

/* ===== 面包屑（变长） ===== */
const crumbs = computed(() => path.value.map((name, i) => ({ label: name, index: i })));

/* ===== 导航（变长 path + 本目录/子目录模式） =====
 * 进入某节点：有 coverage json → 展示本目录详情（可再下拉切到子目录）；
 * 无 coverage json（agg 汇总层）→ 只能展示子目录列表。
 */
function enterNode(nodePath: string[]) {
  path.value = [...nodePath];
  coverData.value = null;
  subSel.value = '';
  const node = findNode(path.value);
  ensureMode(node);
  if (mode.value === 'detail' && hasCoverage(node)) loadDetail(path.value);
}
function goRoot() { path.value = []; mode.value = 'children'; coverData.value = null; subSel.value = ''; }
// 列表卡片点击：进入该子节点
function goNode(name: string) { enterNode([...path.value, name]); }
// 下拉切换：仅子目录名 → 进入该子目录（返回上一级走面包屑）；选中后清空受控值，保证下拉可再次展开
function switchSub(value: string) {
  subSel.value = '';
  enterNode([...path.value, value]);
}
function goOpFromTreemap(p: { path: string[] }) { enterNode(p.path); }
function crumbGo(i: number) { enterNode(path.value.slice(0, i + 1)); }

/* ===== 加载树 ===== */
async function loadTree() {
  loading.value = true;
  path.value = [];
  mode.value = 'children';
  coverData.value = null;
  try {
    const res = await fetch(treeUrl());
    const data = await res.json();
    if (data && data.versions) tree.value = data;
    else if (data && data.error) { ElMessage.error('加载覆盖率失败: ' + data.error); tree.value = { versions: [] }; }
    else tree.value = { versions: [] };
  } catch (e: any) { ElMessage.error('加载目录列表失败: ' + e.message); }
  finally { loading.value = false; }
}

/* ===== 加载节点详情（coverage + analysis） ===== */
async function loadDetail(nodePath: string[]) {
  detailLoading.value = true;
  coverData.value = null;
  uncoveredReasons.value = {};
  coveredUncoveredLines.value = {};
  expandedFunc.value = '';
  try {
    const [covRes, anaRes] = await Promise.all([
      fetch(detailUrl(nodePath, 'coverage')).then(r => r.json()),
      fetch(detailUrl(nodePath, 'analysis')).then(r => r.json()).catch(() => null),
    ]);
    if (covRes && !covRes.error) coverData.value = covRes;
    else ElMessage.error('加载覆盖率失败: ' + (covRes && covRes.error ? covRes.error : '未知'));
    if (anaRes && !anaRes.error && anaRes.content) {
      const parsed = parseAnalysis(anaRes.content);
      uncoveredReasons.value = parsed.reasons;
      coveredUncoveredLines.value = parsed.linesDetail;
    }
  } catch (e: any) { ElMessage.error('请求失败: ' + e.message); }
  finally { detailLoading.value = false; }
}

/* ===== 任务内嵌：加载任务列表 + 选中任务详情 ===== */
async function loadRunList() {
  loading.value = true;
  try {
    const res = await fetch(runsUrl());
    const data = await res.json();
    runList.value = (data && data.runs) ? data.runs : [];
  } catch (e: any) { ElMessage.error('加载任务列表失败: ' + e.message); }
  finally { loading.value = false; }
}

async function selectRun(runId: string) {
  runSel.value = runId;
  detailLoading.value = true;
  coverData.value = null;
  uncoveredReasons.value = {};
  coveredUncoveredLines.value = {};
  expandedFunc.value = '';
  try {
    const [covRes, anaRes] = await Promise.all([
      fetch(runDetailUrl(runId, 'coverage')).then(r => r.json()),
      fetch(runDetailUrl(runId, 'analysis')).then(r => r.json()).catch(() => null),
    ]);
    if (covRes && !covRes.error) coverData.value = covRes;
    else ElMessage.error('加载覆盖率失败: ' + (covRes && covRes.error ? covRes.error : '未知'));
    if (anaRes && !anaRes.error && anaRes.content) {
      const parsed = parseAnalysis(anaRes.content);
      uncoveredReasons.value = parsed.reasons;
      coveredUncoveredLines.value = parsed.linesDetail;
    }
  } catch (e: any) { ElMessage.error('请求失败: ' + e.message); }
  finally { detailLoading.value = false; }
}

/* ===== 详情派生（复用 analysis.md 解析） ===== */
function curGran() {
  const g = coverData.value && coverData.value.granularities;
  if (!g) return null;
  return g.func || g.file || null;
}
const moduleCoverage = computed(() => {
  const g = curGran();
  return (g && g.coverage) ? g.coverage : {};
});

const uncoveredRows = computed(() => {
  const g = curGran();
  if (!g) return [];
  const rows: any[] = [];
  const uf = g.uncoveredFuncs;
  if (uf && typeof uf === 'object') {
    Object.keys(uf).forEach(mod => {
      (uf[mod] || []).forEach((f: any) => {
        const r = uncoveredReasons.value[f.name] || {};
        rows.push({ module: mod, name: f.name || '', dir: f.dir || '', category: r.category || '', coverable: normalizeCover(r.coverable || ''), reason: r.reason || '' });
      });
    });
  }
  return rows;
});

const coveredFuncRows = computed(() => {
  const g = curGran();
  if (!g || !g.coveredFuncLineDetail) return [];
  const cfl = g.coveredFuncLineDetail;
  // 兼容两种结构：新结构为列表 [{func,file,dir,total_lines,cov_lines,cov_rate,covered_lines,uncovered_lines}]，旧结构为 {items:[...]}
  const list = Array.isArray(cfl) ? cfl : (cfl.items || []);
  const linesMap = coveredUncoveredLines.value;
  // cov_rate 为 100 视为已全部覆盖：忽略 uncovered_lines，且不展示进未覆盖行详情列表
  return list
    .filter((it: any) => {
      const r = Number(it.cov_rate);
      return !(Number.isFinite(r) && r >= 100);
    })
    .map((it: any) => {
      const name = it.func || it.name || '';
      const la = linesMap[name] || {};
      return {
        name,
        dir: it.dir || it.file || '',
        lineCount: it.cov_lines ?? it.lineCount ?? 0,
        totalLines: it.total_lines ?? it.lineCount ?? 0,
        covRate: it.cov_rate,
        uncoveredLines: Array.isArray(it.uncovered_lines) ? it.uncovered_lines : (la.uncoveredLines || ''),
        hasAnalysis: !!(la.details && la.details.length),
      };
    });
});
function funcDetails(name: string) {
  const la = coveredUncoveredLines.value[name];
  return (la && la.details) ? la.details : [];
}
function toggleFunc(name: string) { expandedFunc.value = (expandedFunc.value === name) ? '' : name; }

function normalizeCover(cover: string) {
  const s = (cover || '').trim();
  if (s.includes('是') || s.includes('✅')) return '是';
  if (s.includes('可能') || s.includes('⚠')) return '可能';
  return s ? '否' : '未提供';
}
function coverTypeOf(cover: string) {
  const n = normalizeCover(cover);
  return n === '是' ? 'yes' : n === '可能' ? 'maybe' : 'no';
}
function coverTagType(cover: string) {
  const n = normalizeCover(cover);
  return n === '是' ? 'success' : n === '可能' ? 'warning' : 'danger';
}

/* ===== analysis.md 解析（C 节未覆盖原因 + D 节已覆盖函数行级） ===== */
function stripCode(s: string) { return s.replace(/`/g, '').trim(); }
function splitMdRow(line: string) { return line.replace(/^\||\|$/g, '').split('|').map(s => s.trim()); }
function parseMdTable(lines: string[], start: number, end: number) {
  const header: string[] = [];
  const data: string[][] = [];
  let inTable = false;
  for (let i = start; i < end && i < lines.length; i++) {
    const t = lines[i].trim();
    if (!t.startsWith('|')) { if (inTable) break; continue; }
    const cells = splitMdRow(t);
    if (!inTable) { header.push(...cells); inTable = true; if (i + 1 < end && lines[i + 1].trim().match(/^\|[\s:|-]+\|$/)) i++; }
    else data.push(cells);
  }
  return { header, data };
}
function parseAnalysis(content: string) {
  const reasons: Record<string, any> = {};
  const linesDetail: Record<string, any> = {};
  const lines = content.split('\n');
  let cStart = -1, dStart = -1;
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim();
    if (t.startsWith('## ') && t.includes('未覆盖函数') && cStart < 0) cStart = i;
    if (t.startsWith('## ') && t.includes('已覆盖函数') && t.includes('未覆盖行')) dStart = i;
  }
  if (cStart >= 0) {
    const cEnd = dStart > cStart ? dStart : lines.length;
    const rows = parseMdTable(lines, cStart, cEnd);
    if (rows.header.length) {
      const nameIdx = rows.header.findIndex(h => h.includes('函数'));
      const catIdx = rows.header.findIndex(h => h.includes('分类'));
      const coverIdx = rows.header.findIndex(h => h.includes('能否覆盖'));
      const reasonIdx = rows.header.findIndex(h => h.includes('原因'));
      rows.data.forEach(r => {
        const name = stripCode(r[nameIdx] || '');
        if (name) reasons[name] = { category: r[catIdx] || '', coverable: r[coverIdx] || '', reason: r[reasonIdx] || '' };
      });
    }
  }
  if (dStart >= 0) {
    let i = dStart + 1;
    while (i < lines.length) {
      const t = lines[i].trim();
      if (t.startsWith('## ')) break;
      if (t.startsWith('### ')) {
        const funcName = stripCode(t.replace(/^###\s+/, '').split('(')[0].trim());
        let j = i + 1;
        let coveredLines = '', uncoveredLines = '';
        let tblStart = -1;
        while (j < lines.length) {
          const tj = lines[j].trim();
          if (tj.startsWith('### ') || tj.startsWith('## ')) break;
          if (tj.startsWith('|') && tblStart < 0) tblStart = j;
          const m = tj.match(/已覆盖\s*(\d+)\s*行.*未覆盖\s*(\d+)\s*行/);
          if (m) { coveredLines = m[1]; uncoveredLines = m[2]; }
          j++;
        }
        if (funcName && tblStart >= 0) {
          const rows = parseMdTable(lines, tblStart, j);
          if (rows.header.length) {
            const col = (name: string) => rows.header.findIndex(h => h.includes(name));
            const details = rows.data.map(r => ({
              lineno: (r[col('行号')] || '').trim(),
              src: r[col('源码')] || '',
              category: r[col('分类')] || '',
              coverable: (r[col('能否覆盖')] || '').trim(),
              reason: r[col('原因')] || '',
              suggest: r[col('建议')] || '',
            }));
            linesDetail[funcName] = { coveredLines, uncoveredLines, details };
          }
        }
        i = j;
      } else i++;
    }
  }
  return { reasons, linesDetail };
}

/* ===== 初始化 ===== */
onMounted(loadTree);
</script>

<template>
<div class="legacy-review">
  <TopBar view="coverage">
    <TaskPicker />
  </TopBar>

  <div class="cov-wrap">
    <!-- 数据源切换 -->
    <div class="src-tabs">
      <button :class="{ on: source === 'global' }" @click="switchSource('global')">全局最终报告</button>
      <button :class="{ on: source === 'run' }" @click="switchSource('run')">任务内嵌报告</button>
    </div>

    <!-- 面包屑（变长） -->
    <nav class="crumb" v-if="!loading">
      <template v-if="source === 'global'">
        <span class="step" :class="{ cur: path.length === 0 }" @click="goRoot">整体统计</span>
        <template v-for="(c, i) in crumbs" :key="i">
          <span class="sep">›</span>
          <span class="step" :class="{ cur: i === crumbs.length - 1 }" @click="crumbGo(i)">{{ c.label }}</span>
        </template>
      </template>
      <template v-else>
        <span class="step" :class="{ cur: !runSel }" @click="runSel = ''">任务内嵌</span>
        <template v-if="runSel">
          <span class="sep">›</span>
          <span class="step cur">{{ runSel }}</span>
        </template>
      </template>
    </nav>

    <!-- 整体 KPI（仅全局根视图） -->
    <div class="kpi-row" v-if="source === 'global' && path.length === 0 && !loading">
      <div class="kpi" v-for="k in kpiItems" :key="k.l">
        <div class="num" :style="k.color ? { color: k.color } : {}">{{ k.n }}<span class="unit">{{ k.u }}</span></div>
        <div class="lbl">{{ k.l }}</div>
      </div>
    </div>

    <!-- 主内容 -->
    <main class="cov-main">
      <div v-if="loading" class="cov-empty">正在加载覆盖率数据…</div>

      <!-- 全局：根视图（treemap/列表） -->
      <template v-else-if="source === 'global' && path.length === 0">
        <div class="view-tabs">
          <el-switch
            v-model="viewMode"
            active-value="treemap"
            inactive-value="list"
            active-text="矩阵视图"
            inactive-text="列表视图"
          />
        </div>
        <div v-if="viewMode === 'treemap'" class="tm-grid">
          <CoverageTreemap :tree="tree" metric="func" @select="goOpFromTreemap" />
          <CoverageTreemap :tree="tree" metric="line" @select="goOpFromTreemap" />
        </div>
        <CoverageList v-else title="版本" tag="点击查看该版本下的算子库" :items="childCards" @select="goNode($event.key)" />
      </template>

      <!-- 全局：节点视图（本目录详情 / 子目录列表，可下拉切换） -->
      <template v-else-if="source === 'global' && path.length > 0">
        <div class="node-toolbar">
          <span class="nt-label">{{ path.join(' / ') }}</span>
          <el-select
            v-if="showDropdown"
            v-model="subSel"
            size="small"
            class="nt-select"
            placeholder="切换子目录"
            @change="switchSub"
          >
            <el-option v-for="o in subOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>

        <!-- 本目录详情：覆盖率 + 未覆盖详情 -->
        <template v-if="mode === 'detail' && hasCoverage(current)">
          <div v-if="detailLoading" class="cov-empty">加载中…</div>
          <template v-else-if="coverData">
            <div class="detail-head"><h2>{{ detailTitle }}</h2><span class="detail-meta">{{ detailMeta }}</span></div>
            <div class="panel">
              <h3>模块覆盖率</h3>
              <ModuleTreemap :coverage="moduleCoverage" />
            </div>
            <div class="panel" v-if="uncoveredRows.length">
              <h3>未覆盖函数（analysis.md 提取）</h3>
              <table>
                <thead><tr><th>模块</th><th>函数名</th><th>分类</th><th>能否覆盖</th><th>原因</th></tr></thead>
                <tbody>
                  <tr v-for="(r, i) in uncoveredRows" :key="i">
                    <td><span class="pill" :class="r.module === 'kernel' ? 'warn' : ''">{{ r.module }}</span></td>
                    <td class="mono">{{ r.name }}</td>
                    <td>{{ r.category || '-' }}</td>
                    <td><span class="pill" :class="coverTypeOf(r.coverable)">{{ r.coverable || '-' }}</span></td>
                    <td class="reason">{{ r.reason || '-' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="panel" v-if="coveredFuncRows.length">
              <h3>已覆盖函数中的未覆盖行</h3>
              <table>
                <thead><tr><th>函数名</th><th>文件</th><th>覆盖率</th><th>未覆盖行号</th></tr></thead>
                <tbody>
                  <template v-for="(r, i) in coveredFuncRows" :key="i">
                    <tr class="expandable" @click="toggleFunc(r.name)">
                      <td class="mono">{{ r.name }}</td>
                      <td class="mono path">{{ r.dir }}</td>
                      <td :style="{ color: rateHex(r.covRate ?? 0) }">{{ r.covRate != null ? r.covRate + '%' : '-' }}</td>
                      <td class="mono unc-lines">{{ Array.isArray(r.uncoveredLines) ? r.uncoveredLines.join(', ') : (r.uncoveredLines || '-') }}</td>
                    </tr>
                    <tr v-if="expandedFunc === r.name && funcDetails(r.name).length">
                      <td colspan="4">
                        <div v-for="(d, j) in funcDetails(r.name)" :key="j" class="line-card" :class="coverTypeOf(d.coverable)">
                          <span class="lc-lineno mono">L{{ d.lineno }}</span>
                          <span class="lc-src mono" v-if="d.src">{{ d.src }}</span>
                          <span class="lc-cat" v-if="d.category">{{ d.category }}</span>
                          <span class="lc-reason" v-if="d.reason">原因：{{ d.reason }}</span>
                          <span class="lc-suggest" v-if="d.suggest">建议：{{ d.suggest }}</span>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
            <div class="hint" v-if="!uncoveredRows.length && !coveredFuncRows.length">该目录有覆盖率数据；未覆盖详情需对应 analysis.md（本目录未提供时仅展示覆盖率）。</div>
          </template>
          <div v-else class="cov-empty">该目录无有效覆盖率数据</div>
        </template>

        <!-- 子目录列表 -->
        <template v-else>
          <CoverageList
            :title="path.join(' / ') + ' → 子项'"
            tag="点击子项下钻；带 coverage 的目录可看本目录覆盖率 + 未覆盖详情"
            :items="childCards"
            @select="goNode($event.key)" />
        </template>
      </template>

      <!-- 任务内嵌：任务名称列表 -->
      <template v-else-if="source === 'run' && !showDetail">
        <CoverageList title="任务" tag="点击任务名称查看该任务内嵌覆盖率" :items="runCards" @select="selectRun($event.key)" />
      </template>

      <!-- 详情（全局与任务内嵌共用） -->
      <template v-else-if="showDetail">
        <button v-if="source === 'run'" class="back-btn" @click="runSel = ''">← 返回任务列表</button>
        <div v-if="detailLoading" class="cov-empty">加载中…</div>
        <template v-else-if="coverData">
          <div class="detail-head"><h2>{{ detailTitle }}</h2><span class="detail-meta">{{ detailMeta }}</span></div>

          <div class="panel">
            <h3>模块覆盖率</h3>
            <ModuleTreemap :coverage="moduleCoverage" />
          </div>

          <div class="panel" v-if="uncoveredRows.length">
            <h3>未覆盖函数（analysis.md 提取）</h3>
            <table>
              <thead><tr><th>模块</th><th>函数名</th><th>分类</th><th>能否覆盖</th><th>原因</th></tr></thead>
              <tbody>
                <tr v-for="(r, i) in uncoveredRows" :key="i">
                  <td><span class="pill" :class="r.module === 'kernel' ? 'warn' : ''">{{ r.module }}</span></td>
                  <td class="mono">{{ r.name }}</td>
                  <td>{{ r.category || '-' }}</td>
                  <td><span class="pill" :class="coverTypeOf(r.coverable)">{{ r.coverable || '-' }}</span></td>
                  <td class="reason">{{ r.reason || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="panel" v-if="coveredFuncRows.length">
            <h3>已覆盖函数中的未覆盖行</h3>
            <table>
              <thead><tr><th>函数名</th><th>文件</th><th>覆盖率</th><th>未覆盖行号</th></tr></thead>
              <tbody>
                <template v-for="(r, i) in coveredFuncRows" :key="i">
                  <tr class="expandable" @click="toggleFunc(r.name)">
                    <td class="mono">{{ r.name }}</td>
                    <td class="mono path">{{ r.dir }}</td>
                    <td :style="{ color: rateHex(r.covRate ?? 0) }">{{ r.covRate != null ? r.covRate + '%' : '-' }}</td>
                    <td class="mono unc-lines">{{ Array.isArray(r.uncoveredLines) ? r.uncoveredLines.join(', ') : (r.uncoveredLines || '-') }}</td>
                  </tr>
                  <tr v-if="expandedFunc === r.name && funcDetails(r.name).length">
                    <td colspan="4">
                      <div v-for="(d, j) in funcDetails(r.name)" :key="j" class="line-card" :class="coverTypeOf(d.coverable)">
                        <span class="lc-lineno mono">L{{ d.lineno }}</span>
                        <span class="lc-src mono" v-if="d.src">{{ d.src }}</span>
                        <span class="lc-cat" v-if="d.category">{{ d.category }}</span>
                        <span class="lc-reason" v-if="d.reason">原因：{{ d.reason }}</span>
                        <span class="lc-suggest" v-if="d.suggest">建议：{{ d.suggest }}</span>
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>

          <div class="hint">目录级合并 / 算子叶子层才有覆盖率 + analysis.md 分析文档；汇总层（版本/库/域）仅展示预聚合覆盖率。</div>
        </template>
        <div v-else class="cov-empty">该算子无有效覆盖率数据</div>
      </template>
    </main>
  </div>
</div>
</template>

<style scoped>
* { margin: 0; padding: 0; box-sizing: border-box; }

.legacy-review {
  --bg: var(--wb-bg); --panel-bg: var(--wb-card); --border: var(--wb-line); --border-light: var(--wb-line-soft);
  --text: var(--wb-ink); --text-secondary: var(--wb-muted); --text-muted: var(--wb-faint);
  --accent: var(--wb-blue); --accent-light: var(--wb-blue-soft);
  --green: var(--wb-green); --red: var(--wb-red); --orange: var(--wb-orange);
  --code-bg: var(--wb-code-bg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden;
}
.mono { font-family: "JetBrains Mono", Consolas, monospace; }

/* ===== 顶栏由共享 TopBar 提供 ===== */

.cov-wrap { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

/* 数据源切换 */
.src-tabs { display: flex; gap: 8px; padding: 14px 22px 0; flex-shrink: 0; }
.src-tabs button { border: 1px solid var(--border); background: var(--panel-bg); color: var(--text-secondary); border-radius: 8px; padding: 6px 14px; font-size: 13px; cursor: pointer; }
.src-tabs button.on { color: var(--accent); border-color: var(--accent); background: var(--accent-light); font-weight: 600; }

/* 视图切换（列表/树图） */
.view-tabs { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
/* 节点工具栏：当前路径 + 本目录/子目录切换下拉 */
.node-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.node-toolbar .nt-label { font-size: 14px; font-weight: 650; color: var(--wb-ink); }
.node-toolbar .nt-select { width: 240px; }
.tm-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 1200px) { .tm-grid { grid-template-columns: 1fr; } }

/* 面包屑 */
.crumb { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; color: var(--text-secondary);
  margin: 14px 22px 12px; padding: 9px 14px; background: var(--panel-bg); border: 1px solid var(--border); border-radius: 10px; flex-shrink: 0; }
.crumb .step { cursor: pointer; }
.crumb .step:hover { color: var(--accent); }
.crumb .step.cur { color: var(--text); font-weight: 600; cursor: default; }
.crumb .sep { color: var(--text-muted); }

/* KPI */
.kpi-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; padding: 0 22px; margin-bottom: 14px; flex-shrink: 0; }
@media (max-width: 1100px) { .kpi-row { grid-template-columns: repeat(3, 1fr); } }
.kpi { position: relative; background: var(--panel-bg); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px 11px; box-shadow: var(--wb-shadow); overflow: hidden; }
.kpi::before { content: ""; position: absolute; left: 0; top: 0; right: 0; height: 3px; background: linear-gradient(90deg, var(--accent), var(--wb-blue-line)); }
.kpi .num { font-size: 28px; font-weight: 750; line-height: 1.1; font-family: "JetBrains Mono", monospace; color: var(--text); }
.kpi .unit { font-size: 13px; font-weight: 600; color: var(--text-muted); margin-left: 2px; }
.kpi .lbl { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }

/* 主内容 */
.cov-main { flex: 1; overflow-y: auto; padding: 0 22px 28px; }
.back-btn { border: 1px solid var(--border); background: var(--panel-bg); color: var(--accent); border-radius: 8px; padding: 6px 14px; font-size: 13px; cursor: pointer; margin-bottom: 14px; }
.back-btn:hover { background: var(--wb-hover); }

/* 详情 */
.detail-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.detail-head h2 { margin: 0; font-size: 18px; color: var(--text); }
.detail-meta { font-size: 12px; color: var(--text-muted); }
.panel { background: var(--panel-bg); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; margin-bottom: 14px; box-shadow: var(--wb-shadow); }
.panel h3 { margin: 0 0 10px; font-size: 14px; color: var(--text); }
.mods { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 12px; }
.mod { border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; }
.mod-rings { display: flex; justify-content: space-around; flex-wrap: wrap; gap: 8px; }
.mod h4 { margin: 0 0 7px; font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .5px; display: flex; justify-content: space-between; }
.mod h4 span { font-size: 11px; font-weight: 400; }

table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border-light); }
th { color: var(--text-secondary); font-weight: 600; background: var(--code-bg); }
td { color: var(--text); }
td.reason { color: var(--text-secondary); font-size: 12px; }
td.path { color: var(--text-secondary); font-size: 12px; }
tr.expandable { cursor: pointer; }
tr.expandable:hover td { background: var(--wb-hover); }

.pill { font-size: 11px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; background: var(--code-bg); color: var(--text-secondary); }
.pill.warn { background: var(--wb-orange-soft); color: var(--orange); }
.pill.yes { background: var(--wb-green-soft); color: var(--green); }
.pill.maybe { background: var(--wb-orange-soft); color: var(--orange); }
.pill.no { background: var(--wb-red-soft); color: var(--red); }

.line-card { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 10px; border: 1px solid var(--border-light); border-left: 3px solid var(--border); border-radius: 6px; margin: 4px 0; font-size: 12px; }
.line-card.yes { border-left-color: var(--green); background: #f0fdf4; }
.line-card.maybe { border-left-color: var(--orange); background: #fffbeb; }
.line-card.no { border-left-color: var(--border); }
.lc-lineno { font-weight: 600; background: var(--code-bg); padding: 1px 8px; border-radius: 4px; }
.lc-src { color: var(--text-secondary); background: var(--code-bg); padding: 2px 8px; border-radius: 4px; }
.lc-cat { color: var(--text-muted); font-size: 11px; }
.lc-reason { color: var(--text-secondary); }
.lc-suggest { color: var(--accent); }
.unc-lines { color: var(--red); font-size: 12px; }

.hint { font-size: 12px; color: var(--text-secondary); background: var(--accent-light); border-left: 3px solid var(--accent); padding: 8px 10px; border-radius: 4px; margin: 10px 0; }
.cov-empty { text-align: center; color: var(--text-muted); padding: 80px 0; font-size: 14px; }
</style>