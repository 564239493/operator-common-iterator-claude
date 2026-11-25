<script setup lang="ts">
/* 布局③：模块联动下钻（方案A 包含关系卡片图 + 未覆盖函数/已覆盖函数中的未覆盖行 列表过滤联动）
 * 卡片：op = host + kernel（op 点击复位为全部）；host ⊇ tiling（tiling 虚线子卡内嵌 host）
 * 联动：点击卡片过滤右侧两个列表并高亮当前模块、置灰其余模块
 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';

const props = defineProps<{
  coverage: any;                 // granularities.func.coverage：{op,host,tiling,kernel} 各带 count + metrics[{name,covered,total,rate}]
  uncovered: any[];              // 未覆盖函数行：{module,name,dir,category,coverable,reason}
  covered: any[];                // 已覆盖函数中的未覆盖行：{name,dir,lineCount,totalLines,covRate,uncoveredLines,hasAnalysis}
  linesMap: Record<string, any>; // analysis.md 行级明细：name -> {details:[{lineno,src,category,coverable,reason,suggest}]}
  expanded: string;              // 当前展开的函数名（由页面持有）
  coverTypeOf: (c: string) => string;
  rateHex: (r: number) => string;
  hintAll: string;               // 两列表均空且未过滤时展示的提示
}>();
const emit = defineEmits<{ (e: 'toggle', name: string): void }>();

const selModule = ref<'all' | 'host' | 'tiling' | 'kernel'>('all');

/* 列表分页（可切换每页条数；表格区域高度自适应占满屏幕，超出内部滚动） */
const pageSize = ref(6);
const pageUnc = ref(1);
const pageCov = ref(1);

function toggle(name: string) { emit('toggle', name); }
function details(name: string) { const la = props.linesMap[name]; return (la && la.details) || []; }

function shortMetric(name: string) {
  const s = (name || '');
  if (s.includes('函数')) return '函数';
  if (s.includes('分支')) return '分支';
  return '行';
}
function fmtMetric(m: any) { return (m && m.total) ? Math.round(m.rate) + '%' : '—'; }
function mWidth(m: any) { return (m && m.total) ? Math.round(m.rate) + '%' : '0%'; }
function rateCls(r: number) { return r >= 80 ? 'ok' : r >= 50 ? 'mid' : 'bad'; }

// 由文件路径推断模块（kernel_meta/… → kernel；含 tiling → tiling；其余 host）
function moduleOfDir(dir: string) {
  const d = (dir || '').toLowerCase();
  if (d.includes('kernel_meta') || d.startsWith('kernel')) return 'kernel';
  if (d.includes('tiling')) return 'tiling';
  return 'host';
}
function cardCls(k: 'all' | 'host' | 'tiling' | 'kernel') {
  const s = selModule.value;
  if (s === 'all') return { sel: k === 'all', dim: false };
  // 选中某模块时：op 卡（all）恒置灰；host↔tiling 同族（包含关系）不互相置灰，仅另一族 kernel 置灰
  if (k === 'all') return { sel: false, dim: true };
  if (s === 'host') return { sel: k === 'host', dim: k === 'kernel' };
  if (s === 'tiling') return { sel: k === 'tiling', dim: k === 'kernel' };
  return { sel: k === 'kernel', dim: k !== 'kernel' }; // s === 'kernel'
}

const cards = computed(() => {
  const mc = props.coverage || {};
  const metrics = (k: string) => (mc[k] && mc[k].metrics) || [];
  const counts = (k: string) => ({
    unc: props.uncovered.filter((r: any) => r.module === k).length,
    det: props.covered.filter((r: any) => moduleOfDir(r.dir) === k).length,
  });
  return {
    op: { label: '算子整体', note: 'op 由 host 和 kernel 组成 · 点击复位为全部', metrics: metrics('op') },
    host: { label: 'Host 侧代码', note: `未覆盖 ${counts('host').unc} · 行明细 ${counts('host').det}`, metrics: metrics('host') },
    tiling: { label: 'tiling 逻辑（host 内）', note: `未覆盖 ${counts('tiling').unc} · 行明细 ${counts('tiling').det}`, metrics: metrics('tiling') },
    kernel: { label: 'Kernel 侧代码', note: `未覆盖 ${counts('kernel').unc} · 行明细 ${counts('kernel').det}`, metrics: metrics('kernel') },
  };
});

const filteredUncovered = computed(() =>
  selModule.value === 'all' ? props.uncovered : props.uncovered.filter((r: any) => r.module === selModule.value));
const filteredCovered = computed(() =>
  selModule.value === 'all' ? props.covered : props.covered.filter((r: any) => moduleOfDir(r.dir) === selModule.value));

const pagedUncovered = computed(() => filteredUncovered.value.slice((pageUnc.value - 1) * pageSize.value, pageUnc.value * pageSize.value));
const pagedCovered = computed(() => filteredCovered.value.slice((pageCov.value - 1) * pageSize.value, pageCov.value * pageSize.value));

// 数据、过滤条件或每页条数变化时回到第一页
watch([filteredUncovered, filteredCovered, pageSize], () => { pageUnc.value = 1; pageCov.value = 1; });

/* 面板高度兜底：实测视口剩余高度，保证面板（含分页器）不超出屏幕 */
const rootEl = ref<HTMLElement | null>(null);
const availH = ref(0);
function measure() {
  const el = rootEl.value;
  if (!el) return;
  availH.value = Math.max(320, Math.floor(window.innerHeight - el.getBoundingClientRect().top) - 8);
}
onMounted(() => { measure(); window.addEventListener('resize', measure); });
onBeforeUnmount(() => window.removeEventListener('resize', measure));
</script>

<template>
  <div ref="rootEl" class="panel" :style="availH ? { maxHeight: availH + 'px' } : null">
    <h3>模块覆盖率</h3>
    <div class="cov-lay3">
      <div class="cards-col">
        <!-- op 整体（点击复位为全部） -->
        <div class="mod-card m-op" :class="cardCls('all')" @click="selModule = 'all'">
          <div class="m-head">
            <span class="pill m-pill op">op</span><span>{{ cards.op.label }}</span>
            <span class="m-note">{{ cards.op.note }}</span>
          </div>
          <div v-for="m in cards.op.metrics" :key="m.name" class="m-row">
            <span class="lb">{{ shortMetric(m.name) }}</span>
            <span class="bar"><i :class="rateCls(m.rate)" :style="{ width: mWidth(m) }"></i></span>
            <span class="val" :class="rateCls(m.rate)">{{ fmtMetric(m) }}</span>
          </div>
        </div>

        <!-- host（内嵌 tiling） -->
        <div class="mod-card m-host" :class="cardCls('host')" @click="selModule = 'host'">
          <div class="m-head">
            <span class="pill m-pill host">host</span><span>{{ cards.host.label }}</span>
            <span class="m-note">{{ cards.host.note }}</span>
          </div>
          <div v-for="m in cards.host.metrics" :key="m.name" class="m-row">
            <span class="lb">{{ shortMetric(m.name) }}</span>
            <span class="bar"><i :class="rateCls(m.rate)" :style="{ width: mWidth(m) }"></i></span>
            <span class="val" :class="rateCls(m.rate)">{{ fmtMetric(m) }}</span>
          </div>
          <div class="tiling-wrap" :class="cardCls('tiling')" @click.stop="selModule = 'tiling'">
            <div class="m-head">
              <span class="pill m-pill tiling">tiling</span><span>{{ cards.tiling.label }}</span>
              <span class="m-note">{{ cards.tiling.note }}</span>
            </div>
            <div v-for="m in cards.tiling.metrics" :key="m.name" class="m-row">
              <span class="lb">{{ shortMetric(m.name) }}</span>
              <span class="bar"><i :class="rateCls(m.rate)" :style="{ width: mWidth(m) }"></i></span>
              <span class="val" :class="rateCls(m.rate)">{{ fmtMetric(m) }}</span>
            </div>
          </div>
        </div>

        <!-- kernel -->
        <div class="mod-card m-kernel" :class="cardCls('kernel')" @click="selModule = 'kernel'">
          <div class="m-head">
            <span class="pill m-pill kernel">kernel</span><span>{{ cards.kernel.label }}</span>
            <span class="m-note">{{ cards.kernel.note }}</span>
          </div>
          <div v-for="m in cards.kernel.metrics" :key="m.name" class="m-row">
            <span class="lb">{{ shortMetric(m.name) }}</span>
            <span class="bar"><i :class="rateCls(m.rate)" :style="{ width: mWidth(m) }"></i></span>
            <span class="val" :class="rateCls(m.rate)">{{ fmtMetric(m) }}</span>
          </div>
        </div>
      </div>

      <div class="lists-col">
        <div class="filter-bar" v-if="selModule !== 'all'">
          <span>已按模块过滤：</span>
          <span class="pill m-pill" :class="selModule">{{ selModule }}</span>
          <span class="reset" @click="selModule = 'all'">点击 op 卡或此处复位为全部</span>
        </div>

        <div class="panel" v-if="filteredUncovered.length">
          <h3>未覆盖函数<span class="sec-cnt">（analysis.md 提取 · {{ filteredUncovered.length }} 项）</span></h3>
          <div class="tbl-scroll">
            <table>
              <thead><tr><th>模块</th><th>函数名</th><th>分类</th><th>能否覆盖</th><th>原因</th></tr></thead>
              <tbody>
                <tr v-for="(r, i) in pagedUncovered" :key="i">
                  <td><span class="pill m-pill" :class="r.module">{{ r.module }}</span></td>
                  <td class="mono">{{ r.name }}</td>
                  <td>{{ r.category || '-' }}</td>
                  <td><span class="pill" :class="coverTypeOf(r.coverable)">{{ r.coverable || '-' }}</span></td>
                  <td class="reason">{{ r.reason || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="pager-row" v-if="filteredUncovered.length">
            <el-pagination v-model:current-page="pageUnc" :page-size="pageSize" :page-sizes="[6, 10, 20, 50]" :total="filteredUncovered.length" layout="total, sizes, prev, pager, next" small />
          </div>
        </div>

        <div class="panel" v-if="filteredCovered.length">
          <h3>已覆盖函数中的未覆盖行<span class="sec-cnt">（{{ filteredCovered.length }} 项）</span></h3>
          <div class="tbl-scroll">
            <table>
              <thead><tr><th>函数名</th><th>文件</th><th>覆盖率</th><th>未覆盖行号</th></tr></thead>
              <tbody>
                <template v-for="(r, i) in pagedCovered" :key="i">
                  <tr class="expandable" @click="toggle(r.name)">
                    <td class="mono">{{ r.name }}</td>
                    <td class="mono path">{{ r.dir }}</td>
                    <td :style="{ color: rateHex(r.covRate ?? 0) }">{{ r.covRate != null ? r.covRate + '%' : '-' }}</td>
                    <td class="mono unc-lines">{{ Array.isArray(r.uncoveredLines) ? r.uncoveredLines.join(', ') : (r.uncoveredLines || '-') }}</td>
                  </tr>
                  <tr v-if="expanded === r.name && details(r.name).length">
                    <td colspan="4">
                      <div v-for="(d, j) in details(r.name)" :key="j" class="line-card" :class="coverTypeOf(d.coverable)">
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
          <div class="pager-row" v-if="filteredCovered.length">
            <el-pagination v-model:current-page="pageCov" :page-size="pageSize" :page-sizes="[6, 10, 20, 50]" :total="filteredCovered.length" layout="total, sizes, prev, pager, next" small />
          </div>
        </div>

        <div class="hint" v-if="selModule === 'all' && !filteredUncovered.length && !filteredCovered.length">{{ hintAll }}</div>
        <div class="hint" v-else-if="selModule !== 'all' && !filteredUncovered.length && !filteredCovered.length">模块「{{ selModule }}」暂无未覆盖函数或未覆盖行明细记录。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel { background: var(--panel-bg); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; box-shadow: var(--wb-shadow);
  flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; }
.panel h3 { margin: 0 0 10px; font-size: 14px; color: var(--text); flex-shrink: 0; }
.sec-cnt { font-weight: 400; font-size: 12px; color: var(--text-muted); margin-left: 4px; }

/* 布局③：左卡片图 + 右双列表（flex 链条填满可用高度，不依赖视口估算）
 * 关键：grid 显式行轨道 minmax(0,1fr) —— 否则隐式行 auto 按内容撑高，flex 高度传不下去，
 * 列表面板（flex-basis:0; min-height:0）会塌陷为 0 */
.cov-lay3 { display: grid; grid-template-columns: 340px 1fr; grid-template-rows: minmax(0, 1fr); gap: 14px; align-items: stretch; flex: 1 1 auto; min-height: 0; }
@media (max-width: 1100px) {
  .cov-lay3 { grid-template-columns: 1fr; grid-template-rows: auto; }
  .lists-col { height: auto; min-height: 0; }
  .lists-col .panel { flex: none; }
  .tbl-scroll { flex: none; max-height: 45vh; }
}
.cards-col { display: flex; flex-direction: column; gap: 12px; }
.lists-col { min-width: 0; display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.lists-col .panel { margin-bottom: 0; flex: 1 1 0; min-height: 0; display: flex; flex-direction: column; }
.lists-col .panel h3 { flex-shrink: 0; }

/* 表格高度自适应：占满列表列剩余高度，超出部分内部滚动 */
.tbl-scroll { flex: 1 1 0; min-height: 0; overflow-y: auto; border-radius: 8px; }
.tbl-scroll table thead th { position: sticky; top: 0; z-index: 1; }
.pager-row { flex-shrink: 0; display: flex; justify-content: flex-end; margin-top: 10px; }
.pager-row :deep(.el-pagination) { --el-pagination-button-bg-color: transparent; --el-pagination-hover-color: var(--accent); }

/* 方案A 模块卡片（可点击联动；flex 均分卡片列高度，三卡高度合理分配） */
.mod-card { border: 1.5px solid var(--border); border-radius: 12px; padding: 20px; background: var(--panel-bg);
  flex: 1 1 auto; cursor: pointer; transition: opacity .15s, box-shadow .15s, border-color .15s; }
.mod-card.m-op { border-color: var(--wb-blue-line); background: var(--wb-blue-soft); }
.mod-card.m-host { border-color: var(--wb-green-line); background: var(--wb-green-soft); flex-grow: 1.35; }
.mod-card.m-kernel { border-color: var(--wb-orange-line); background: var(--wb-orange-soft); }
/* 选中态：仅单层边框高亮（去 outline，避免双层 border） */
.mod-card.sel { border-color: var(--accent); box-shadow: var(--wb-shadow); }
.mod-card.dim { opacity: .45; }
.m-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-weight: 700; font-size: 15px; margin-bottom: 30px; }
.m-head .pill { font-size: 12px; padding: 2px 9px; }
.m-note { font-weight: 400; font-size: 11px; color: var(--text-secondary); margin-left: auto; }
.m-row { display: grid; grid-template-columns: 34px 1fr 46px; align-items: center; gap: 8px; font-size: 12px; margin: 5px 0; }
.m-row .lb { color: var(--text-secondary); }
.bar { height: 8px; border-radius: 4px; background: var(--border-light); overflow: hidden; position: relative; }
.bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; }
.bar i.ok { background: var(--green); } .bar i.mid { background: var(--orange); } .bar i.bad { background: var(--red); }
.m-row .val { text-align: right; font-weight: 700; font-variant-numeric: tabular-nums; }
.val.ok { color: var(--green); } .val.mid { color: var(--orange); } .val.bad { color: var(--red); }

/* tiling 内嵌子卡（虚线框，紫色；选中态单层边框高亮） */
.tiling-wrap { border: 1.5px dashed var(--wb-purple-line); border-radius: 10px; padding: 10px 12px; margin-top: 30px;
  background: var(--wb-purple-soft); cursor: pointer; transition: opacity .15s, box-shadow .15s, border-color .15s; }
.tiling-wrap.sel { border-color: var(--wb-purple); box-shadow: var(--wb-shadow); }
.tiling-wrap.dim { opacity: .45; }

/* 过滤提示条 */
.filter-bar { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--text-secondary);
  background: var(--accent-light); border: 1px solid var(--wb-blue-line); border-radius: 8px; padding: 5px 12px; }
.filter-bar .reset { color: var(--accent); font-weight: 600; cursor: pointer; }
.filter-bar .reset:hover { text-decoration: underline; }

/* 模块彩色 pill */
.pill { font-size: 11px; padding: 1px 8px; border-radius: 10px; white-space: nowrap; background: var(--code-bg); color: var(--text-secondary); }
.pill.m-pill.op { background: var(--wb-blue-soft); color: var(--wb-blue); }
.pill.m-pill.host { background: var(--wb-green-soft); color: var(--wb-green); }
.pill.m-pill.tiling { background: var(--wb-purple-soft); color: var(--wb-purple); }
.pill.m-pill.kernel { background: var(--wb-orange-soft); color: var(--wb-orange); }
.pill.yes { background: var(--wb-green-soft); color: var(--green); }
.pill.maybe { background: var(--wb-orange-soft); color: var(--orange); }
.pill.no { background: var(--wb-red-soft); color: var(--red); }

.mono { font-family: "JetBrains Mono", Consolas, monospace; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border-light); }
th { color: var(--text-secondary); font-weight: 600; background: var(--code-bg); }
td { color: var(--text); }
td.reason { color: var(--text-secondary); font-size: 12px; }
td.path { color: var(--text-secondary); font-size: 12px; }
tr.expandable { cursor: pointer; }
tr.expandable:hover td { background: var(--wb-hover); }

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

.hint { font-size: 12px; color: var(--text-secondary); background: var(--accent-light); border-left: 3px solid var(--accent); padding: 8px 10px; border-radius: 4px; }
</style>