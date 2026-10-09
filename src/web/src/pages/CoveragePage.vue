<script>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { useTask } from '../composables/useTask';
import TaskPicker from '../components/task/TaskPicker.vue';
import TopBar from '../components/shell/TopBar.vue';


export default {
    components: { TaskPicker, TopBar },
    setup() {
        // ---- 目录列表 ----
        const coverDirs = ref([]);
        const currentCoverDir = ref('');
        const coverSearch = ref('');
        // 任务上下文：全站共享选择（?run= 已由路由守卫种入），空串 = 未选任务，浏览全局目录；
        // 只看所选任务内嵌的 runs/<run>/ops_cov_report/，路径关联、零名称匹配
        const { selectedRunId } = useTask();
        const runContext = computed(() => selectedRunId.value.trim());

        // 按上下文切换数据源：有任务上下文走任务内嵌接口，否则浏览全局目录
        const dirsUrl = () => runContext.value
            ? '/api/review/runs/' + encodeURIComponent(runContext.value) + '/cover/dirs'
            : '/api/review/cover/dirs';
        const detailUrl = (dirName, kind) => runContext.value
            ? '/api/review/runs/' + encodeURIComponent(runContext.value) + '/cover/' + encodeURIComponent(dirName) + '/' + kind
            : '/api/review/cover/' + encodeURIComponent(dirName) + '/' + kind;
        const detailLoading = ref(false);
        const coverData = ref(null);
        const granularity = ref('func');
        const activePanels = ref(['uncovered']);
        // 从 analysis.md 提取的结构化解释
        const uncoveredReasons = ref({});    // {funcName → {category, coverable, reason}}
        const coveredUncoveredLines = ref({}); // {funcName → {coveredLines, uncoveredLines, details:[...]}}
        const expandedFunc = ref(''); // 已覆盖函数表展开行详情的函数名

        const filteredDirs = computed(() => {
            const q = coverSearch.value.trim().toLowerCase();
            if (!q) return coverDirs.value;
            return coverDirs.value.filter(d =>
                (d.operator || '').toLowerCase().includes(q) ||
                (d.dir_name || '').toLowerCase().includes(q)
            );
        });


        // ---- 模块与指标 ----
        const coverModules = ['host', 'kernel', 'tiling', 'op'];

        function curGran() {
            const g = coverData.value && coverData.value.granularities;
            if (!g) return null;
            return g[granularity.value] || g.file || g.func || null;
        }

        function metricsOf(mod) {
            const g = curGran();
            if (!g || !g.coverage || !g.coverage[mod]) return [];
            return g.coverage[mod].metrics || [];
        }

        function modCount(mod) {
            const g = curGran();
            if (!g || !g.coverage || !g.coverage[mod]) return null;
            return g.coverage[mod].count;
        }

        function rateColor(rate) {
            if (rate >= 80) return '#10b981';
            if (rate >= 50) return '#f59e0b';
            return '#ef4444';
        }

        // ---- 算子信息 ----
        const opDomain = computed(() => {
            const oi = coverData.value && coverData.value.operatorInfo;
            return oi && oi.domain ? oi.domain : '';
        });
        const opDir = computed(() => {
            const oi = coverData.value && coverData.value.operatorInfo;
            return oi && oi.dir ? oi.dir : '';
        });

        // ---- 未覆盖函数 (关联 analysis.md 提取的原因) ----
        const uncoveredRows = computed(() => {
            const g = curGran();
            if (!g) return [];
            const rows = [];
            const uf = g.uncoveredFuncs;
            const reasons = uncoveredReasons.value;
            if (uf && typeof uf === 'object') {
                Object.keys(uf).forEach(mod => {
                    (uf[mod] || []).forEach(f => {
                        const r = reasons[f.name] || {};
                        rows.push({
                            module: mod, name: f.name || '', dir: f.dir || '',
                            category: r.category || '',
                            coverable: normalizeCover(r.coverable || ''),
                            reason: r.reason || '',
                        });
                    });
                });
            }
            return rows;
        });

        // ---- 已覆盖函数行详情 (关联 analysis.md D 节) ----
        const coveredFuncRows = computed(() => {
            const g = curGran();
            if (!g || !g.coveredFuncLineDetail) return [];
            const linesMap = coveredUncoveredLines.value;
            return (g.coveredFuncLineDetail.items || []).map(it => {
                const la = linesMap[it.name] || {};
                return {
                    name: it.name || '',
                    dir: it.dir || '',
                    fullPath: it.fullPath || '',
                    coveredLines: it.covered_lines || [],
                    lineCount: it.lineCount || 0,
                    lineStr: it.lineStr || '',
                    uncoveredLines: la.uncoveredLines || '',
                    hasAnalysis: !!(la.details && la.details.length),
                };
            });
        });

        function funcDetails(name) {
            const la = coveredUncoveredLines.value[name];
            return (la && la.details) ? la.details : [];
        }

        function toggleFunc(name) {
            expandedFunc.value = (expandedFunc.value === name) ? '' : name;
        }

        // 规范化"能否覆盖": 去 emoji, 统一为 否/可能/是
        function normalizeCover(cover) {
            const s = (cover || '').trim();
            if (s.includes('是') || s.includes('✅')) return '是';
            if (s.includes('可能') || s.includes('⚠')) return '可能';
            return s ? '否' : '未提供';
        }

        function coverTypeOf(cover) {
            const n = normalizeCover(cover);
            if (n === '是') return 'yes';
            if (n === '可能') return 'maybe';
            return 'no';
        }

        function coverTagType(cover) {
            const n = normalizeCover(cover);
            if (n === '是') return 'success';
            if (n === '可能') return 'warning';
            return 'danger';
        }

        // ---- 解析 analysis.md: 提取 C 节(未覆盖函数原因) + D 节(已覆盖函数未覆盖行详情) ----
        // 纯 JS 行解析, 不依赖 marked/CDN
        function parseAnalysis(content) {
            const reasons = {};
            const linesDetail = {};
            const lines = content.split('\n');

            // 找 ## 章节行号
            let cStart = -1, dStart = -1;
            for (let i = 0; i < lines.length; i++) {
                const t = lines[i].trim();
                if (t.startsWith('## ') && t.includes('未覆盖函数') && cStart < 0) cStart = i;
                if (t.startsWith('## ') && t.includes('已覆盖函数') && t.includes('未覆盖行')) dStart = i;
            }

            // C 节: 从 cStart 到下一个 ## 之间的表格 (表头含 函数/分类/能否覆盖/原因)
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
                        if (name) reasons[name] = {
                            category: r[catIdx] || '',
                            coverable: r[coverIdx] || '',
                            reason: r[reasonIdx] || '',
                        };
                    });
                }
            }

            // D 节: 每个 ### funcname 后跟 "已覆盖 X 行，未覆盖 Y 行" + 行级 table
            if (dStart >= 0) {
                let i = dStart + 1;
                while (i < lines.length) {
                    const t = lines[i].trim();
                    if (t.startsWith('## ')) break; // 到下一章节结束
                    if (t.startsWith('### ')) {
                        const funcName = stripCode(t.replace(/^###\s+/, '').split('(')[0].trim());
                        // 向后找: 行级摘要 + table (到下一个 ### 或 ##)
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
                            if (rows.header.includes('行号') && rows.header.includes('能否覆盖')) {
                                const details = rows.data.map(r => ({
                                    lineno: (r[0] || '').trim(),
                                    src: r[1] || '',
                                    reason: r[2] || '',
                                    coverable: (r[3] || '').trim(),
                                    suggest: r[4] || '',
                                }));
                                linesDetail[funcName] = {coveredLines, uncoveredLines, details};
                            }
                        }
                        i = j;
                    } else {
                        i++;
                    }
                }
            }

            return {reasons, linesDetail};
        }

        // 解析 MD 表格: 从 lines[start] 开始, 到 lines[end] 前, 提取表头+数据行
        function parseMdTable(lines, start, end) {
            const header = [];
            const data = [];
            let inTable = false;
            for (let i = start; i < end && i < lines.length; i++) {
                const t = lines[i].trim();
                if (!t.startsWith('|')) {
                    if (inTable) break; // 表格结束
                    continue;
                }
                const cells = splitMdRow(t);
                if (!inTable) {
                    header.push(...cells);
                    inTable = true;
                    // 下一行是分隔符 |---|, 跳过
                    if (i + 1 < end && lines[i + 1].trim().match(/^\|[\s:|-]+\|$/)) i++;
                } else {
                    data.push(cells);
                }
            }
            return {header, data};
        }

        // 拆分单行 |a|b|c| → ['a','b','c']
        function splitMdRow(line) {
            return line.replace(/^\||\|$/g, '').split('|').map(s => s.trim());
        }

        // 去掉行内代码反引号, 取纯文本: `set_dimNum` → set_dimNum
        function stripCode(s) {
            return s.replace(/`/g, '').trim();
        }

        // ---- 选择目录 + 加载数据 ----
        async function selectCoverDir(d) {
            if (currentCoverDir.value === d.dir_name && coverData.value) return;
            currentCoverDir.value = d.dir_name;
            detailLoading.value = true;
            coverData.value = null;
            uncoveredReasons.value = {};
            coveredUncoveredLines.value = {};
            expandedFunc.value = '';
            activePanels.value = ['uncovered'];
            try {
                const [covRes, anaRes] = await Promise.all([
                    fetch(detailUrl(d.dir_name, 'coverage')).then(r => r.json()),
                    fetch(detailUrl(d.dir_name, 'analysis')).then(r => r.json()).catch(() => null),
                ]);
                if (covRes && !covRes.error) {
                    coverData.value = covRes;
                    const gs = covRes.granularities || {};
                    granularity.value = gs.func ? 'func' : (gs.file ? 'file' : 'func');
                } else {
                    ElMessage.error('加载覆盖率失败: ' + (covRes && covRes.error ? covRes.error : '未知'));
                }
                if (anaRes && !anaRes.error && anaRes.content) {
                    const parsed = parseAnalysis(anaRes.content);
                    uncoveredReasons.value = parsed.reasons;
                    coveredUncoveredLines.value = parsed.linesDetail;
                }
            } catch (e) {
                ElMessage.error('请求失败: ' + e.message);
            } finally {
                detailLoading.value = false;
            }
        }

        function escapeHtml(s) {
            return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        // ---- 初始化 ----
        let dirsTimer = null;

        async function loadDirs() {
            const list = await (await fetch(dirsUrl())).json();
            coverDirs.value = Array.isArray(list) ? list : [];
        }

        // 上下文变化（含初始装载）统一重载目录列表并重置当前选择
        watch(runContext, async () => {
            currentCoverDir.value = '';
            coverData.value = null;
            uncoveredReasons.value = {};
            coveredUncoveredLines.value = {};
            expandedFunc.value = '';
            try {
                await loadDirs();
                if (coverDirs.value.length) {
                    await selectCoverDir(coverDirs.value[0]);
                } else if (runContext.value) {
                    ElMessage.info('该任务暂无覆盖报告（runs/' + runContext.value + '/ops_cov_report/ 不存在或为空）');
                }
            } catch (e) {
                ElMessage.error('加载目录列表失败: ' + e.message);
            }
        }, { immediate: true });

        onMounted(async () => {
            // 目录列表 30s 自动刷新：新生成的覆盖报告自动出现，不改变当前选择
            dirsTimer = setInterval(async () => {
                try {
                    const prev = coverDirs.value.length;
                    await loadDirs();
                    if (!currentCoverDir.value && coverDirs.value.length) {
                        await selectCoverDir(coverDirs.value[0]);
                    } else if (runContext.value && !prev && coverDirs.value.length) {
                        await selectCoverDir(coverDirs.value[0]);
                    }
                } catch (e) { /* 静默重试 */ }
            }, 30000);
        });

        onBeforeUnmount(() => { if (dirsTimer) clearInterval(dirsTimer); });



        return {
            coverDirs, filteredDirs, coverSearch, currentCoverDir, detailLoading,
            coverData, granularity, activePanels,
            uncoveredReasons, coveredUncoveredLines, expandedFunc,
            coverModules, metricsOf, modCount, rateColor,
            opDomain, opDir, uncoveredRows, coveredFuncRows,
            runContext,

            selectCoverDir, funcDetails, toggleFunc, coverTypeOf, normalizeCover, coverTagType,
        };
    }
};
</script>
<template>
<div class="legacy-review">
    <!-- 顶栏: 共享 TopBar（品牌/导航/主题）+ 任务选择 -->
    <TopBar view="coverage">
        <TaskPicker />
    </TopBar>

    <p class="report-note">独立覆盖报告 · 函数、语句和分支覆盖率分别统计。
        <template v-if="runContext">正在查看任务 <b>{{ runContext }}</b> 内嵌的覆盖报告（runs/{{ runContext }}/ops_cov_report/），按路径关联，不做名称匹配。</template>
        <template v-else>未携带任务上下文，正在浏览全局覆盖报告目录（项目根 ops_cov_report/）。</template>
        
    </p>
    <div class="cover-main">
        <!-- 左侧: 覆盖率数据目录列表 -->
        <div class="cover-sidebar">
            <div class="sidebar-header">
                <span>覆盖率数据</span>
                <el-tag size="small" type="info">{{ filteredDirs.length }}</el-tag>
            </div>
            <div class="sidebar-search">
                <el-input v-model="coverSearch" placeholder="筛选算子/目录" size="small" clearable
                          prefix-icon="Search"></el-input>
            </div>
            <div class="sidebar-list">
                <div v-if="!coverDirs.length" class="sidebar-empty">暂无覆盖率数据</div>
                <div v-for="d in filteredDirs" :key="d.dir_name" class="sidebar-item"
                     :class="{active: d.dir_name === currentCoverDir}" @click="selectCoverDir(d)">
                    <div class="si-name">{{ d.operator }}</div>
                    <div class="si-dir">{{ d.dir_name }}</div>
                </div>
            </div>
        </div>

        <!-- 右侧: 详情 -->
        <div class="cover-detail">
            <div v-if="detailLoading" class="detail-loading">加载中...</div>
            <div v-else-if="!currentCoverDir" class="detail-empty">
                <div class="empty-icon">📊</div>
                <div>请从左侧选择覆盖率数据</div>
            </div>
            <template v-else-if="coverData">
                <!-- 算子信息 -->
                <div class="op-info-bar">
                    <div class="op-info-left">
                        <span class="op-name">{{ coverData.operator }}</span>
                        <el-tag size="small" type="info" v-if="opDomain">{{ opDomain }}</el-tag>
                        <span class="op-dir" v-if="opDir">{{ opDir }}</span>
                    </div>
                    <!-- 粒度切换 -->
                    <el-radio-group v-model="granularity" size="small">
                        <el-radio-button label="file">file 粒度</el-radio-button>
                        <el-radio-button label="func">func 粒度</el-radio-button>
                    </el-radio-group>
                </div>

                <div class="metric-modules"><section v-for="mod in coverModules" :key="mod" class="metric-module"><h3>{{ mod }}</h3><div v-for="m in metricsOf(mod)" :key="m.name" class="coverage-metric"><div>{{ m.name }} <b>{{ m.rate }}%</b></div><progress :value="m.covered" :max="m.total || 1"></progress><small>{{ m.covered }} / {{ m.total }}</small></div></section></div>
                <!-- 折叠面板: 未覆盖函数(含原因) / 已覆盖函数(含未覆盖行详情) -->
                <el-collapse v-model="activePanels" class="cover-panels">
                    <el-collapse-item name="uncovered" v-if="uncoveredRows.length">
                        <template #title>
                            <div class="panel-title">
                                <span>未覆盖函数</span>
                                <el-tag size="small" type="danger">{{ uncoveredRows.length }}</el-tag>
                            </div>
                        </template>
                        <el-table :data="uncoveredRows" class="op-table" max-height="480" size="small" row-key="name">
                            <el-table-column type="expand">
                                <template #default="{ row }">
                                    <div class="uc-expand" v-if="row.reason">
                                        <div class="uc-reason-label">原因说明</div>
                                        <div class="uc-reason-text">{{ row.reason }}</div>
                                    </div>
                                    <div class="uc-expand-empty" v-else>暂无分析说明</div>
                                </template>
                            </el-table-column>
                            <el-table-column label="模块" width="70">
                                <template #default="{ row }">
                                    <el-tag size="small" :type="row.module === 'kernel' ? 'warning' : 'info'">
                                        {{ row.module }}
                                    </el-tag>
                                </template>
                            </el-table-column>
                            <el-table-column label="函数名" min-width="240" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ row.name }}</span></template>
                            </el-table-column>
                            <el-table-column label="分类" min-width="160" show-overflow-tooltip>
                                <template #default="{ row }">
                                    <span class="uc-category">{{ row.category || '-' }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="能否覆盖" width="90" align="center">
                                <template #default="{ row }">
                                    <el-tag size="small" :type="coverTagType(row.coverable)" effect="light">
                                        {{ row.coverable || '-' }}
                                    </el-tag>
                                </template>
                            </el-table-column>
                        </el-table>
                    </el-collapse-item>

                    <el-collapse-item name="covered" v-if="coveredFuncRows.length">
                        <template #title>
                            <div class="panel-title">
                                <span>已覆盖函数（未覆盖行详情）</span>
                                <el-tag size="small" type="success">{{ coveredFuncRows.length }}</el-tag>
                            </div>
                        </template>
                        <el-table :data="coveredFuncRows" class="op-table" size="small" row-key="name">
                            <el-table-column type="expand">
                                <template #default="{ row }">
                                    <div class="cv-expand" v-if="row.hasAnalysis">
                                        <div class="cv-summary">
                                            <span>已覆盖 <b>{{ row.lineCount }}</b> 行</span>
                                            <span v-if="row.uncoveredLines" class="cv-uncovered-num">未覆盖 <b>{{ row.uncoveredLines }}</b> 行</span>
                                            <span class="cv-total-lines">{{ funcDetails(row.name).length }} 条行级分析</span>
                                        </div>
                                        <div class="line-cards">
                                            <div v-for="(d, i) in funcDetails(row.name)" :key="i"
                                                 class="line-card" :class="coverTypeOf(d.coverable)">
                                                <div class="lc-head">
                                                    <span class="lc-lineno">L{{ d.lineno }}</span>
                                                    <el-tag size="small" :type="coverTagType(d.coverable)" effect="light">
                                                        {{ normalizeCover(d.coverable) }}
                                                    </el-tag>
                                                </div>
                                                <div class="lc-src mono" v-if="d.src" v-html="d.src"></div>
                                                <div class="lc-reason" v-if="d.reason"><span class="lc-label">原因</span><span v-html="d.reason"></span></div>
                                                <div class="lc-suggest" v-if="d.suggest"><span class="lc-label">建议</span><span v-html="d.suggest"></span></div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="uc-expand-empty" v-else>暂无行级分析</div>
                                </template>
                            </el-table-column>
                            <el-table-column label="函数名" min-width="240" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ row.name }}</span></template>
                            </el-table-column>
                            <el-table-column label="文件" min-width="280" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono cm-path">{{ row.dir }}</span></template>
                            </el-table-column>
                            <el-table-column label="已覆盖" width="70" align="center">
                                <template #default="{ row }">{{ row.lineCount }} 行</template>
                            </el-table-column>
                            <el-table-column label="未覆盖" width="80" align="center">
                                <template #default="{ row }">
                                    <span v-if="row.uncoveredLines" class="cv-uncovered-tag">{{ row.uncoveredLines }} 行</span>
                                    <span v-else>-</span>
                                </template>
                            </el-table-column>
                        </el-table>
                    </el-collapse-item>
                </el-collapse>
            </template>
            <div v-else class="detail-empty">
                <div class="empty-icon">⚠️</div>
                <div>该目录无有效覆盖率数据</div>
            </div>
        </div>
    </div>
</div>
</template>
<style scoped>
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

.legacy-review {
    /* 旧静态页遗留变量统一映射到全站设计令牌（--wb-*），暗色主题随之生效 */
    --bg: var(--wb-bg);
    --panel-bg: var(--wb-card);
    --border: var(--wb-line);
    --border-light: var(--wb-line-soft);
    --text: var(--wb-ink);
    --text-secondary: var(--wb-muted);
    --text-muted: var(--wb-faint);
    --accent: var(--wb-blue);
    --accent-light: var(--wb-blue-soft);
    --green: var(--wb-green);
    --red: var(--wb-red);
    --orange: var(--wb-orange);
    --code-bg: var(--wb-code-bg);
    --code: var(--wb-muted);
}

.legacy-review {
    height: 100%;
}

.legacy-review {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    font-size: 14px;
}

.legacy-review {
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.mono {
    font-family: "JetBrains Mono", "Fira Code", Consolas, "Courier New", monospace;
}

/* ===== 顶栏由共享 TopBar 提供 ===== */

/* ===== 主体: 左右分栏 ===== */
.cover-main {
    flex: 1;
    display: flex;
    overflow: hidden;
}

/* ===== 左侧栏 ===== */
.cover-sidebar {
    width: 260px;
    flex-shrink: 0;
    background: var(--panel-bg);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
}

.sidebar-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 16px 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-muted);
    letter-spacing: 0.3px;
    text-transform: uppercase;
}

.sidebar-search {
    padding: 0 12px 12px;
}

.sidebar-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 8px 12px;
}

.sidebar-empty {
    text-align: center;
    color: var(--text-muted);
    padding: 40px 0;
    font-size: 13px;
}

.sidebar-item {
    padding: 10px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.15s;
    margin-bottom: 3px;
    border: 1px solid transparent;
}

.sidebar-item:hover {
    background: var(--code-bg);
}

.sidebar-item.active {
    background: var(--accent-light);
    border-color: var(--wb-blue-line);
}

.sidebar-item.active .si-name {
    color: var(--accent);
}

.si-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.si-dir {
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-family: "JetBrains Mono", Consolas, monospace;
}

/* ===== 右侧详情 ===== */
.cover-detail {
    flex: 1;
    overflow-y: auto;
    padding: 22px 28px 28px;
}

.detail-loading, .detail-empty {
    text-align: center;
    color: var(--text-muted);
    padding: 100px 0;
    font-size: 14px;
}

.empty-icon {
    font-size: 44px;
    margin-bottom: 14px;
    opacity: 0.5;
}

/* 算子信息栏 */
.op-info-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
}

.op-info-left {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
}

.op-name {
    font-size: 17px;
    font-weight: 600;
    color: var(--text);
}

.op-dir {
    font-size: 12px;
    color: var(--text-muted);
    font-family: "JetBrains Mono", Consolas, monospace;
    background: var(--code-bg);
    padding: 2px 8px;
    border-radius: 4px;
}

/* 覆盖率卡片: 4 列等宽占满 */
.cov-cards {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 22px;
}

@media (max-width: 960px) {
    .cov-cards {
        grid-template-columns: repeat(2, 1fr);
    }
}

.cov-card {
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
    transition: box-shadow 0.15s;
}

.cov-card:hover {
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.cov-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-light);
}

.cov-mod-name {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--text);
    letter-spacing: 0.5px;
}

.cov-mod-count {
    font-size: 11px;
    color: var(--text-muted);
    background: var(--code-bg);
    padding: 1px 7px;
    border-radius: 10px;
}

.cov-metric {
    margin-bottom: 9px;
}

.cov-metric:last-child {
    margin-bottom: 0;
}

.cm-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 3px;
}

.cm-name {
    font-size: 12px;
    color: var(--text-secondary);
}

.cm-rate {
    font-size: 13px;
    font-weight: 600;
}

.cm-detail {
    font-size: 10px;
    color: var(--text-muted);
    margin-top: 2px;
    font-family: "JetBrains Mono", Consolas, monospace;
}

/* 覆盖率环形图: 4 模块各一环, 等宽并排 */
.cov-rings {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 22px;
}

@media (max-width: 960px) {
    .cov-rings {
        grid-template-columns: repeat(2, 1fr);
    }
}

.cov-ring-item {
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px 6px;
    transition: box-shadow 0.15s;
}

.cov-ring-item:hover {
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.cov-ring-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 2px;
}

.cov-ring-mod {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--text);
    letter-spacing: 0.5px;
}

.cov-ring-count {
    font-size: 11px;
    color: var(--text-muted);
    background: var(--code-bg);
    padding: 1px 7px;
    border-radius: 10px;
}

.cov-ring-body {
    width: 100%;
    height: 150px;
}

/* 折叠面板 */
.cover-panels {
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
}

.cover-panels .el-collapse-item__header {
    padding: 0 16px;
    height: 44px;
    font-size: 14px;
}

.cover-panels .el-collapse-item__content {
    padding: 0 16px 16px;
}

.cover-panels .el-collapse-item:last-child .el-collapse-item__header {
    border-bottom: none;
}

.panel-title {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 500;
}

.panel-title .el-tag {
    transform: scale(0.9);
}

/* 表格内路径 */
.cm-path {
    font-size: 11px;
    color: var(--text-secondary);
}

.cm-lines {
    font-size: 11px;
    color: var(--text-muted);
}

/* ===== 未覆盖函数展开行 (原因说明) ===== */
.uc-expand {
    padding: 10px 16px;
    background: #fef2f2;
    border-radius: 6px;
}

.uc-reason-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--red);
    margin-bottom: 4px;
    letter-spacing: 0.3px;
}

.uc-reason-text {
    font-size: 12.5px;
    color: var(--text);
    line-height: 1.7;
}

.uc-expand-empty {
    padding: 8px 16px;
    color: var(--text-muted);
    font-size: 12px;
}

.uc-category {
    font-size: 12px;
    color: var(--text-secondary);
}

/* ===== 已覆盖函数展开行 (未覆盖行详情卡片) ===== */
.cv-expand {
    padding: 10px 4px;
}

.cv-summary {
    display: flex;
    gap: 16px;
    align-items: center;
    padding: 8px 12px;
    background: var(--code-bg);
    border-radius: 6px;
    margin-bottom: 10px;
    font-size: 12.5px;
    color: var(--text-secondary);
}

.cv-summary b {
    color: var(--text);
    font-weight: 600;
}

.cv-uncovered-num {
    color: var(--red);
}

.cv-total-lines {
    color: var(--text-muted);
    margin-left: auto;
}

.cv-uncovered-tag {
    color: var(--red);
    font-weight: 600;
}

/* 行级分析卡片 */
.line-cards {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.line-card {
    border: 1px solid var(--border-light);
    border-left: 3px solid var(--border);
    border-radius: 6px;
    padding: 8px 12px;
    background: var(--panel-bg);
    font-size: 12.5px;
    line-height: 1.65;
}

.line-card.yes { border-left-color: var(--green); background: #f0fdf4; }
.line-card.maybe { border-left-color: var(--orange); background: #fffbeb; }
.line-card.no { border-left-color: var(--border); background: #fafbfc; }

.lc-head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
}

.lc-lineno {
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 12px;
    font-weight: 600;
    color: var(--text);
    background: var(--code-bg);
    padding: 1px 8px;
    border-radius: 4px;
}

.lc-src {
    font-size: 11.5px;
    color: var(--code);
    background: var(--code-bg);
    padding: 3px 8px;
    border-radius: 4px;
    margin: 4px 0;
    word-break: break-all;
    border: 1px solid var(--border-light);
}

.lc-reason, .lc-suggest {
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 3px;
}

.lc-label {
    display: inline-block;
    font-size: 10px;
    font-weight: 600;
    color: var(--text-muted);
    background: var(--code-bg);
    padding: 0 5px;
    border-radius: 3px;
    margin-right: 6px;
    vertical-align: 1px;
}

.lc-suggest {
    color: var(--accent);
}

/* 滚动条 */
.sidebar-list::-webkit-scrollbar,
.cover-detail::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}

.sidebar-list::-webkit-scrollbar-track,
.cover-detail::-webkit-scrollbar-track {
    background: transparent;
}

.sidebar-list::-webkit-scrollbar-thumb,
.cover-detail::-webkit-scrollbar-thumb {
    background: #cbd5e1;
    border-radius: 3px;
}

.sidebar-list::-webkit-scrollbar-thumb:hover,
.cover-detail::-webkit-scrollbar-thumb:hover {
    background: var(--text-muted);
}

/* Element Plus 表格微调 */
.op-table {
    font-size: 12.5px;
}

.op-table .el-table__cell {
    padding: 6px 0;
}

.op-table .el-table__expanded-cell {
    padding: 4px 12px 12px;
}

.report-note{padding:8px 24px;color:#64748b;font-size:12px}.metric-modules{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin:18px 0}.metric-module{padding:16px;border:1px solid #e2e8f0;border-radius:12px;background:#fff}.coverage-metric{margin:12px 0;font-size:12px}.coverage-metric b{float:right}.coverage-metric progress{width:100%;height:8px;accent-color:#2774ed}.coverage-metric small{color:#64748b}

</style>
