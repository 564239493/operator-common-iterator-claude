<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import TaskPicker from '../components/task/TaskPicker.vue';
import TopBar from '../components/shell/TopBar.vue';
import CoverageList from '../components/coverage/CoverageList.vue';
import ModuleCovPanel from '../components/coverage/ModuleCovPanel.vue';
import CoverageHierarchyTree from '../components/coverage/CoverageHierarchyTree.vue';

// 数据源：全局最终报告（ops_final_cov_report）默认；任务内嵌（runs/*/ops_cov_report）切换
const source = ref<'global' | 'run'>('global');

/* ===== 数据树（递归：版本/算子库/域/算子目录/算子/产品目录，每节点带 summary + children） ===== */
const tree = ref<{ versions: any[] }>({ versions: [] });
const loading = ref(true);
// 整体统计（权威总数据，来自 cov-data/overview.json；后端暂无该版本完整报告时兜底展示）
const overviewData = ref<any>(null);
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

// 全局视图：列表下钻 / 树形图
const viewMode = ref<'list' | 'tree'>('tree');

/* ===== M1 · 版本 Tab：无「全部」选项，仅各版本（树版本 ∪ 总数据版本） ===== */
const verTab = ref('');
const verTabs = computed(() => {
  const names = new Set<string>();
  tree.value.versions.forEach((v: any) => names.add(v.name));
  (overviewData.value?.versions || []).forEach((v: any) => names.add(v.name));
  return [...names];
});
// 默认选中第一个版本；数据加载前/切换后出现非法值时自动回落到第一个版本
function syncVerTab() {
  const tabs = verTabs.value;
  if (!tabs.length) { verTab.value = ''; return; }
  if (!verTab.value || !tabs.includes(verTab.value)) verTab.value = tabs[0];
}
// 作用域：当前 Tab 下的树版本 / 总数据版本（严格单版本，不展示全部）
const scopeVersions = computed(() => tree.value.versions.filter((v: any) => v.name === verTab.value));
const scopeOverview = computed(() => {
  const ov = (overviewData.value && Array.isArray(overviewData.value.versions)) ? overviewData.value.versions : [];
  return ov.filter((v: any) => v.name === verTab.value);
});

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
  if (!c) return '';
  // 产品层（crumb 第 6 层）：标题 = 算子名 · 产品名（如 aclnnChannelShuffle · A2）；
  // 算子名取父节点（算子目录）目录名，不依赖产品节点自身 operator 字段
  if (path.value.length === 6) {
    const op = path.value[4] || c.operator || '';
    const prod = c.name || '';
    return [op, prod].filter(Boolean).join(' · ');
  }
  // 其它层：目录名优先（产品目录 A2 显示 A2，不显示其内嵌算子名）
  return c.name || c.operator || '';
});
/* ===== 算子层（crumb 第 5 层）：算子目录级（当前节点含算子子项）或算子叶子 =====
 * options = 该层算子名列表；fromLeaf=true 表示当前节点就是算子叶子（同级切换），
 * false 表示当前节点是算子目录、算子层在其下一层（下拉默认选中第一个算子）。
 */
const operatorLayer = computed<{ options: string[]; fromLeaf: boolean } | null>(() => {
  if (source.value !== 'global') return null;
  const c = current.value;
  if (!c) return null;
  const kids = childrenOf(c);
  // 算子目录级（crumb 第 4 层）：子项 = 算子（第 5 层，其下还有产品子目录）→ 算子层在下一层，
  // 始终提供「选择算子」下拉（无论算子数量、也无论算子下是否有产品子目录）
  if (path.value.length === 4 && kids.length) {
    return { options: kids.map((k: any) => k.name), fromLeaf: false };
  }
  // 算子叶子（crumb 第 5 层，无产品子目录）：同级算子 = 父节点（算子目录）子项
  if (path.value.length === 5 && !kids.length) {
    const parent = path.value.length ? findNode(path.value.slice(0, -1)) : null;
    const siblings = parent ? childrenOf(parent).filter((k: any) => !childrenOf(k).length).map((k: any) => k.name) : [];
    if (siblings.length < 2 || !siblings.includes(c.name)) return null;
    return { options: siblings, fromLeaf: true };
  }
  return null;
});
// crumb 算子层下拉的受控值：算子目录级默认不选中（占位「请选择算子」，未选时展示算子目录
// 自身数据）；算子叶子为当前算子（同级切换）
const opSel = computed({
  get: () => {
    const ol = operatorLayer.value;
    if (!ol) return '';
    return ol.fromLeaf ? (current.value ? current.value.name : '') : '';
  },
  set: (v: string) => switchOperator(v),
});
function switchOperator(name: string) {
  const ol = operatorLayer.value;
  if (!ol || !name) return;
  if (ol.fromLeaf) {
    if (current.value && name === current.value.name) return; // 未变化不重进
    enterNode([...path.value.slice(0, -1), name]);
  } else {
    enterNode([...path.value, name]);
  }
}

/* ===== 产品层（crumb 第 6 层）：算子（第 5 层）的子项 = 产品目录 A2/A3/A5，叶子 = 产品明细 =====
 * options = 该算子下产品目录名列表；fromLeaf=false 表示产品层在算子下一层（下拉默认
 * 不选中、占位「请选择产品」，未选时展示算子自身数据）；fromLeaf=true 表示当前节点就是
 * 产品叶子（同级产品切换）。
 * 下拉框无论产品数量多少都展示（1 个也展示，便于明确产品维度）。
 */
const productLayer = computed<{ options: string[]; fromLeaf: boolean } | null>(() => {
  if (source.value !== 'global') return null;
  const c = current.value;
  if (!c) return null;
  const kids = childrenOf(c);
  // 算子（crumb 第 5 层）：子项全部是产品目录叶子 → 产品层在下一层
  if (path.value.length === 5 && kids.length && kids.every((k: any) => !childrenOf(k).length)) {
    return { options: kids.map((k: any) => k.name), fromLeaf: false };
  }
  // 产品叶子（crumb 第 6 层）：同级产品 = 父节点（算子）子项
  if (path.value.length === 6 && !kids.length) {
    const parent = path.value.length ? findNode(path.value.slice(0, -1)) : null;
    const siblings = parent ? childrenOf(parent).map((k: any) => k.name) : [];
    if (!siblings.includes(c.name)) return null;
    return { options: siblings, fromLeaf: true };
  }
  return null;
});
// crumb 产品层下拉的受控值：算子级默认不选中（占位「请选择产品」，未选时展示算子自身
// 数据）；产品叶子为当前产品（同级切换）
const prodSel = computed({
  get: () => {
    const pl = productLayer.value;
    if (!pl) return '';
    return pl.fromLeaf ? (current.value ? current.value.name : '') : '';
  },
  set: (v: string) => switchProduct(v),
});
function switchProduct(name: string) {
  const pl = productLayer.value;
  if (!pl || !name) return;
  if (pl.fromLeaf) {
    if (current.value && name === current.value.name) return; // 未变化不重进
    enterNode([...path.value.slice(0, -1), name]);
  } else {
    enterNode([...path.value, name]);
  }
}
const showDetail = computed(() => (source.value === 'global' && mode.value === 'detail' && hasCoverage(current.value)) || (source.value === 'run' && !!runSel.value));

/* ===== TopBar 下拉选择：covtree=目录树路径 / runs=任务目录 ===== */
function onTopSelect(v: string) {
  if (source.value === 'global') {
    const parts = v ? v.split('/') : [];
    if (parts.length) enterNode(parts);
  } else if (v) {
    selectRun(v);
  }
}

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
  const versions = scopeVersions.value;
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

/* KPI 行（M1）：随版本 Tab 作用域变化（严格单版本）
 * - 版本数 = 当前作用域版本（树 ∪ 总数据）
 * - 算子域/算子库 = 总数据域行数 + 无总数据版本的树算子库数
 * - 函数/行/分支总量卡：已覆盖（树摘要，报告范围）+ 总量（总数据权威合计 + 无总数据版本的树合计），
 *   并给出已覆盖/总量的关系：进度条 + 覆盖率百分比；无覆盖率报告时置「—」并注明原因 */
const kpiItems = computed(() => {
  const s = stats.value;
  const ovVer = scopeOverview.value;
  const ovNames = new Set(ovVer.map((v: any) => v.name));
  const treeLibs = scopeVersions.value
    .filter((v: any) => !ovNames.has(v.name))
    .reduce((a, v: any) => a + childrenOf(v).length, 0);
  const domainCount = ovVer.reduce((a, v) => a + (Array.isArray(v.rows) ? v.rows.length : 0), 0) + treeLibs;
  const covered = { func: 0, line: 0, branch: 0 };
  const total = { func: 0, line: 0, branch: 0 };
  scopeVersions.value.forEach((v: any) => {
    const sm = v.summary || {};
    covered.func += sm.func_covered || 0;
    covered.line += sm.line_covered || 0;
    covered.branch += sm.branch_covered || 0;
    // 无权威总数据版本的树合计兜底进总量
    if (!ovNames.has(v.name)) {
      total.func += sm.func_total || 0;
      total.line += sm.line_total || 0;
      total.branch += sm.branch_total || 0;
    }
  });
  ovVer.forEach((v: any) => {
    total.func += (v.total && v.total.func) || 0;
    total.line += (v.total && v.total.line) || 0;
    total.branch += (v.total && v.total.branch) || 0;
  });
  // 算子总数卡：已执行数（树叶子）并入总量（权威总数据 total.operators），
  // 参考函数总量卡样式（已执行 / 总量 · 执行率）；无总数据版本不展示
  const opTotal = ovVer.reduce((a, v) => a + ((v.total && v.total.operators) || 0), 0);
  const hasTree = scopeVersions.value.length > 0;
  const fmt = (x: number) => x.toLocaleString('en-US');
  const totalCard = (k: 'func' | 'line' | 'branch', label: string, color: string) => {
    const c = covered[k], t = total[k];
    const rate = t ? +(c / t * 100).toFixed(1) : 0;
    return {
      n: hasTree ? fmt(c) : '—',
      u: t ? ' / ' + fmt(t) : '',
      l: label,
      color,
      rate,
      sub: hasTree
        ? (t ? `已覆盖 ${fmt(c)} / 总量 ${fmt(t)} · 覆盖率 ${rate}%` : '暂无总量数据')
        : '该版本暂无覆盖率报告',
    };
  };
  const opCard = () => {
    const c = hasTree ? s.leaves : 0;
    const t = opTotal;
    const rate = t ? +(c / t * 100).toFixed(1) : 0;
    return {
      n: hasTree ? fmt(c) : '—',
      u: t ? ' / ' + fmt(t) : '',
      l: '算子总数',
      color: 'var(--wb-purple)',
      rate,
      sub: hasTree
        ? (t ? `已执行 ${fmt(c)} / 总量 ${fmt(t)} · 执行率 ${rate}%` : '暂无总量数据')
        : '该版本暂无覆盖率报告',
    };
  };
  return [
    { n: domainCount, u: '个', l: ovVer.length ? '算子域' : '算子库', color: '' },
    opCard(),
    totalCard('func', '函数总量', 'var(--wb-green)'),
    totalCard('line', '行总量', 'var(--wb-orange)'),
    totalCard('branch', '分支总量', 'var(--wb-blue)'),
  ];
});

/* 节点 → 卡片（统一归一化，供 CoverageList 渲染）
 * - 有子目录的节点（agg 汇总 / merged 目录）：标题=目录名，点击继续下钻
 * - 无子目录的叶子（真实算子）：标题=算子名，点击进详情
 */
function cardsOf(nodes: any[]): any[] {
  return nodes.map((n: any) => {
    const s = n.summary || {};
    const kids = childrenOf(n);
    const cov = hasCoverage(n);
    // 标题用目录名（产品目录 A2 显示 A2 而非其内嵌算子名）
    const title = n.name || n.operator || '';
    const badge = cov
      ? (n.has_analysis ? '含分析' : '算子')
      : (n.kind === 'merged' ? '目录（合并）' : '汇总');
    const meta = kids.length
      ? `${kids.length} 个子项 · ${countLeaves(n)} 个算子`
      : (n.domain || '-');
    const sub = kids.length ? kids.map((c: any) => (c.name || c.operator || '')).join('、') : (cov ? (n.domain || '') : '');
    return { key: n.name, title, badge, badgeType: 'has-md', meta,
      func: s.func || 0, line: s.line || 0, branch: s.branch || 0, sub: sub || undefined };
  });
}
const childCards = computed(() => cardsOf(currentChildren.value));
// 根视图列表（M1）：单版本 → 该版本算子库/域卡（下钻路径带版本前缀）
const rootListItems = computed(() => {
  const v = tree.value.versions.find((x: any) => x.name === verTab.value);
  return v ? cardsOf(childrenOf(v)) : [];
});
function goRootList(name: string) { enterNode([verTab.value, name]); }
const runCards = computed(() => runList.value.map((r: any) => ({
 key: r.run_id, title: r.run_id,
  badge: r.has_cov_report === false ? '无报告' : r.has_analysis ? '含分析' : '内嵌',
  badgeType: r.has_cov_report === false ? 'no-cov' : r.has_analysis ? 'has-md' : 'cov-only',
  meta: `${r.operator} · ${r.domain || '—'}`, func: r.func, line: r.line, branch: r.branch,
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
  const node = findNode(path.value);
  ensureMode(node);
  if (mode.value === 'detail' && hasCoverage(node)) loadDetail(path.value);
}
function goRoot() { path.value = []; mode.value = 'children'; coverData.value = null; }
// 列表卡片点击：进入该子节点
function goNode(name: string) { enterNode([...path.value, name]); }
function goOpFromTree(p: { path: string[] }) { enterNode(p.path); }
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
    else if (data && data.error) { ElMessage.error('加载覆盖率失败：' + data.error); tree.value = { versions: [] }; }
    else tree.value = { versions: [] };
  } catch (e: any) { ElMessage.error('加载目录列表失败：' + e.message); }
  finally { syncVerTab(); loading.value = false; }
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
    else ElMessage.error('加载覆盖率失败：' + (covRes && covRes.error ? covRes.error : '未知'));
    if (anaRes && !anaRes.error && anaRes.content) {
      const parsed = parseAnalysis(anaRes.content);
      uncoveredReasons.value = parsed.reasons;
      coveredUncoveredLines.value = parsed.linesDetail;
    }
  } catch (e: any) { ElMessage.error('请求失败：' + e.message); }
  finally { detailLoading.value = false; }
}

/* ===== 任务内嵌：加载任务列表 + 选中任务详情 ===== */
async function loadRunList() {
  loading.value = true;
  try {
    const res = await fetch(runsUrl());
    const data = await res.json();
    runList.value = (data && data.runs) ? data.runs : [];
  } catch (e: any) { ElMessage.error('加载任务列表失败：' + e.message); }
  finally { loading.value = false; }
}

async function selectRun(runId: string) {
  runSel.value = runId;
  detailLoading.value = true;
  coverData.value = null;
  uncoveredReasons.value = {};
  coveredUncoveredLines.value = {};
  expandedFunc.value = '';
  const run = runList.value.find((r: any) => r.run_id === runId);
  if (run && run.has_cov_report === false) {
    // 无覆盖率产物的任务：不请求详情，直接提示
    detailLoading.value = false;
    ElMessage.warning('该任务暂无覆盖率报告（缺少 ops_cov_report 产物）');
    return;
  }
  try {
    const [covRes, anaRes] = await Promise.all([
      fetch(runDetailUrl(runId, 'coverage')).then(r => r.json()),
      fetch(runDetailUrl(runId, 'analysis')).then(r => r.json()).catch(() => null),
    ]);
    if (covRes && !covRes.error) coverData.value = covRes;
    else ElMessage.error('加载覆盖率失败：' + (covRes && covRes.error ? covRes.error : '未知'));
    if (anaRes && !anaRes.error && anaRes.content) {
      const parsed = parseAnalysis(anaRes.content);
      uncoveredReasons.value = parsed.reasons;
      coveredUncoveredLines.value = parsed.linesDetail;
    }
  } catch (e: any) { ElMessage.error('请求失败：' + e.message); }
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

/* ===== 加载整体统计（权威总数据） ===== */
async function loadOverview() {
  try {
    const res = await fetch('/cov-data/overview.json');
    overviewData.value = await res.json();
  } catch { overviewData.value = null; }
  finally { syncVerTab(); }
}

/* ===== 初始化 ===== */
onMounted(() => { loadTree(); loadOverview(); });
</script>

<template>
<div class="legacy-review">
  <TopBar view="coverage">
    <TaskPicker
      :mode="source === 'global' ? 'covtree' : 'runs'"
      :tree="tree"
      :list="source === 'run' ? runList : []"
      :model-value="source === 'global' ? path.join('/') : runSel"
      @update:model-value="onTopSelect"
    />
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
          <!-- 算子叶子层（crumb 第 5 层）：下拉切换同级算子 -->
          <el-select
            v-if="operatorLayer && operatorLayer.fromLeaf && i === crumbs.length - 1"
            v-model="opSel"
            size="small"
            class="op-sel"
            popper-class="op-sel-popper"
            placeholder="请选择算子"
          >
            <el-option v-for="o in operatorLayer.options" :key="o" :value="o" :label="o" />
          </el-select>
          <!-- 产品叶子层（crumb 第 6 层）：下拉切换同级产品 -->
          <el-select
            v-else-if="productLayer && productLayer.fromLeaf && i === crumbs.length - 1"
            v-model="prodSel"
            size="small"
            class="op-sel"
            popper-class="op-sel-popper"
            placeholder="请选择产品"
          >
            <el-option v-for="p in productLayer.options" :key="p" :value="p" :label="p" />
          </el-select>
          <span v-else class="step" :class="{ cur: i === crumbs.length - 1 }" @click="crumbGo(i)">{{ c.label }}</span>
        </template>
        <!-- 算子目录级（当前节点含算子子项）：追加第 5 层算子下拉（未选中展示算子目录自身数据） -->
        <template v-if="operatorLayer && !operatorLayer.fromLeaf">
          <span class="sep">›</span>
          <el-select v-model="opSel" size="small" class="op-sel" popper-class="op-sel-popper" placeholder="请选择算子">
            <el-option v-for="o in operatorLayer.options" :key="o" :value="o" :label="o" />
          </el-select>
        </template>
        <!-- 算子级（当前节点含产品子项）：追加第 6 层产品下拉（未选中展示算子自身数据） -->
        <template v-else-if="productLayer && !productLayer.fromLeaf">
          <span class="sep">›</span>
          <el-select v-model="prodSel" size="small" class="op-sel" popper-class="op-sel-popper" placeholder="请选择产品">
            <el-option v-for="p in productLayer.options" :key="p" :value="p" :label="p" />
          </el-select>
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

    <!-- M1 版本 Tab + 整体 KPI（仅全局根视图） -->
    <template v-if="source === 'global' && path.length === 0 && !loading">
      <div class="ver-tabs">
        <el-radio-group v-model="verTab" size="small">
          <el-radio-button v-for="n in verTabs" :key="n" :value="n">{{ n }}</el-radio-button>
        </el-radio-group>
      </div>
      <div class="kpi-row">
        <div class="kpi" v-for="k in kpiItems" :key="k.l">
          <div class="num" :style="k.color ? { color: k.color } : {}">{{ k.n }}<span class="unit">{{ k.u }}</span></div>
          <div class="lbl">{{ k.l }}</div>
          <template v-if="k.rate != null">
            <div class="bar"><i :class="rateCls(k.rate)" :style="{ width: Math.min(100, k.rate) + '%' }"></i></div>
            <div class="sub" :style="k.color ? { color: k.color } : {}">{{ k.sub }}</div>
          </template>
        </div>
      </div>
    </template>

    <!-- 主内容 -->
    <main class="cov-main">
      <div v-if="loading" class="cov-empty">正在加载覆盖率数据…</div>

      <!-- 全局：根视图（树形图/列表） -->
      <template v-else-if="source === 'global' && path.length === 0">
        <div class="view-tabs">
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button value="tree">树形图</el-radio-button>
            <el-radio-button value="list">列表视图</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="viewMode === 'tree'" class="lm-wrap">
          <CoverageHierarchyTree :tree="{ versions: scopeVersions }" @select="goOpFromTree" />
        </div>
        <div v-else class="view-scroll">
          <CoverageList :title="verTab || '版本'" tag="点击查看该版本下的算子库" :items="rootListItems" @select="goRootList($event.key)" />
        </div>
      </template>

      <!-- 全局：节点视图（本目录详情 / 子目录列表） -->
      <template v-else-if="source === 'global' && path.length > 0">
        <!-- 本目录详情：覆盖率 + 未覆盖详情 -->
        <template v-if="mode === 'detail' && hasCoverage(current)">
          <div class="view-scroll view-detail">
            <div v-if="detailLoading" class="cov-empty">加载中…</div>
            <template v-else-if="coverData">
            <div class="detail-head">
              <h2>{{ detailTitle }}</h2>
              <!-- 产品层（第 6 层）：算子名 + 产品标签 -->
              <span v-if="path.length === 6" class="dl-tag prod">产品</span>
            </div>
            <ModuleCovPanel
              :coverage="moduleCoverage"
              :uncovered="uncoveredRows"
              :covered="coveredFuncRows"
              :lines-map="coveredUncoveredLines"
              :expanded="expandedFunc"
              :cover-type-of="coverTypeOf"
              :rate-hex="rateHex"
              hint-all="该目录有覆盖率数据；未覆盖详情需对应 analysis.md（本目录未提供时仅展示覆盖率）。"
              @toggle="toggleFunc" />
          </template>
          <div v-else class="cov-empty">该目录无有效覆盖率数据</div>
          </div>
        </template>

        <!-- 子目录列表 -->
        <template v-else>
          <div class="view-scroll">
            <CoverageList
              :title="path.join(' / ') + ' → 子项'"
              tag="点击子项下钻；含覆盖率的目录可查看本目录覆盖率与未覆盖详情"
              :items="childCards"
              @select="goNode($event.key)" />
          </div>
        </template>
      </template>

      <!-- 任务内嵌：任务名称列表 -->
      <template v-else-if="source === 'run' && !showDetail">
        <div class="view-scroll">
          <CoverageList title="任务" tag="点击任务名称查看该任务内嵌覆盖率" :items="runCards" @select="selectRun($event.key)" />
        </div>
      </template>

      <!-- 详情（全局与任务内嵌共用） -->
      <template v-else-if="showDetail">
        <div class="view-scroll view-detail">
        <button v-if="source === 'run'" class="back-btn" @click="runSel = ''">← 返回任务列表</button>
        <div v-if="detailLoading" class="cov-empty">加载中…</div>
        <template v-else-if="coverData">
          <div class="detail-head"><h2>{{ detailTitle }}</h2></div>

          <ModuleCovPanel
            :coverage="moduleCoverage"
            :uncovered="uncoveredRows"
            :covered="coveredFuncRows"
            :lines-map="coveredUncoveredLines"
            :expanded="expandedFunc"
            :cover-type-of="coverTypeOf"
            :rate-hex="rateHex"
            hint-all="只有目录级合并和算子叶子层才有覆盖率与 analysis.md 分析文档；汇总层（版本、库、域）仅展示预聚合覆盖率。"
            @toggle="toggleFunc" />
        </template>
        <div v-else class="cov-empty">该算子无有效覆盖率数据</div>
        </div>
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
.view-tabs { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; flex-shrink: 0; }
.ver-tabs { display: flex; align-items: center; padding: 0 22px; margin-bottom: 10px; flex-shrink: 0; }

/* 版本 Tab / 视图切换：无容器透明底（激活=蓝字加粗） */
.view-tabs :deep(.el-radio-group), .ver-tabs :deep(.el-radio-group) {
  display: inline-flex; align-items: center; gap: 4px; padding: 0;
  background: transparent; border: none; box-shadow: none;
}
.view-tabs :deep(.el-radio-button), .ver-tabs :deep(.el-radio-button) { margin: 0; padding: 0; border: none; }
.view-tabs :deep(.el-radio-button .el-radio-button__inner), .ver-tabs :deep(.el-radio-button .el-radio-button__inner) {
  border: none; outline: none; box-shadow: none; background: transparent; padding: 5px 14px; height: auto;
  line-height: 18px; font-size: 12.5px; color: var(--text-muted, var(--wb-muted)); font-weight: 500;
  border-radius: 7px; transition: color .15s ease;
}
.view-tabs :deep(.el-radio-button:not(.is-active) .el-radio-button__inner:hover),
.ver-tabs :deep(.el-radio-button:not(.is-active) .el-radio-button__inner:hover) {
  color: var(--accent, var(--wb-blue));
}
.view-tabs :deep(.el-radio-button.is-active .el-radio-button__inner),
.ver-tabs :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: transparent; color: var(--accent, var(--wb-blue)); font-weight: 700;
  box-shadow: none !important; /* 清除 EP 选中态 -1px 左侧发丝线 */
}
.view-tabs :deep(.el-radio-button:focus-visible .el-radio-button__inner),
.ver-tabs :deep(.el-radio-button:focus-visible .el-radio-button__inner) {
  outline: 2px solid var(--wb-blue-line, rgba(39, 116, 237, .4)); outline-offset: -2px;
}
/* crumb 算子层下拉（第 5 层）/ 产品层下拉（第 6 层） */
.op-sel { width: 160px; }
.lm-wrap { width: 100%; flex: 1; min-height: 0; }

/* 面包屑 */
.crumb { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; color: var(--text-secondary);
  margin: 14px 22px 12px; padding: 9px 14px; background: var(--panel-bg); border: 1px solid var(--border); border-radius: 10px; flex-shrink: 0; }
.crumb .step { cursor: pointer; }
.crumb .step:hover { color: var(--accent); }
.crumb .step.cur { color: var(--text); font-weight: 600; cursor: default; }
.crumb .sep { color: var(--text-muted); }

/* KPI */
.kpi-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; padding: 0 22px; margin-bottom: 14px; flex-shrink: 0; }
@media (max-width: 1100px) { .kpi-row { grid-template-columns: repeat(3, 1fr); } }
.kpi { position: relative; background: var(--panel-bg); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px 11px; box-shadow: var(--wb-shadow); overflow: hidden; }
.kpi::before { content: ""; position: absolute; left: 0; top: 0; right: 0; height: 3px; background: linear-gradient(90deg, var(--accent), var(--wb-blue-line)); }
.kpi .num { font-size: 28px; font-weight: 750; line-height: 1.1; font-family: "JetBrains Mono", monospace; color: var(--text); }
.kpi .unit { font-size: 13px; font-weight: 600; color: var(--text-muted); margin-left: 2px; }
.kpi .lbl { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
/* 已覆盖/总量 关系：进度条 + 覆盖率说明 */
.kpi .bar { height: 6px; border-radius: 4px; background: var(--code-bg); overflow: hidden; margin-top: 9px; }
.kpi .bar > i { display: block; height: 100%; border-radius: 4px; }
.kpi .bar > i.ok { background: var(--green); }
.kpi .bar > i.mid { background: var(--orange); }
.kpi .bar > i.bad { background: var(--red); }
.kpi .sub { font-size: 11.5px; color: var(--text-secondary); margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 主内容：高度锁死在屏幕内（flex 收缩），各视图内容内部滚动 */
.cov-main { flex: 1; min-height: 0; overflow-y: hidden; display: flex; flex-direction: column; padding: 0 22px 28px; }
.view-scroll { flex: 1; min-height: 0; overflow-y: auto; }
/* 详情视图：flex 撑满可用高度，面板内部自适应，不超出屏幕 */
.view-detail { height: 100%; display: flex; flex-direction: column; overflow-y: auto; }
.view-detail .back-btn, .view-detail .detail-head { flex-shrink: 0; }
.back-btn { border: 1px solid var(--border); background: var(--panel-bg); color: var(--accent); border-radius: 8px; padding: 6px 14px; font-size: 13px; cursor: pointer; margin-bottom: 14px; }
.back-btn:hover { background: var(--wb-hover); }

/* 详情 */
.detail-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.detail-head h2 { margin: 0; font-size: 18px; color: var(--text); }
/* 产品层 detail-head 的产品标签（与任务选择器/树形图的「产品」标签同色） */
.dl-tag { font-size: 11px; line-height: 1; padding: 3px 8px; border-radius: 9px; white-space: nowrap; }
.dl-tag.prod { color: var(--wb-purple); background: var(--wb-purple-soft); }
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

<!-- 面包屑算子下拉（popper 传送到 body，需全局样式）：选项左侧留白加大，文字不贴边框 -->
<style>
.op-sel-popper .el-select-dropdown__item { padding-left: 32px; padding-right: 32px; }
</style>