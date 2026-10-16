const {createApp, ref, computed, onMounted, onBeforeUnmount, nextTick, watch} = Vue;
const ElMessage = ElementPlus.ElMessage;

const app = createApp({
    setup() {
        // ---- 目录列表 ----
        const coverDirs = ref([]);
        const currentCoverDir = ref('');
        const coverSearch = ref('');
        const detailLoading = ref(false);
        const coverData = ref(null);
        const granularity = ref('func');
        const activePanels = ref(['uncovered']);
        // 从 analysis.md 提取的结构化解释
        const uncoveredReasons = ref({});    // {funcName → {category, coverable, reason}}
        const coveredUncoveredLines = ref({}); // {funcName → {coveredLines, uncoveredLines, details:[...]}}
        const expandedFunc = ref(''); // 已覆盖函数表展开行详情的函数名

        // ---- ECharts 环形图: 每模块一环 ----
        const ringEls = ref({});   // {mod: domEl}
        const ringInstances = {};  // {mod: echartsInstance}
        const chartReady = computed(() => {
            if (!coverData.value || !window.echarts) return false;
            const g = coverData.value.granularities || {};
            return !!g[granularity.value];
        });

        // 单模块环形图 option: covered vs (total-covered), 环心综合率, tooltip 明细
        function ringOption(mod) {
            const g = coverData.value && coverData.value.granularities[granularity.value];
            if (!g || !g.coverage || !g.coverage[mod]) return null;
            const metrics = g.coverage[mod].metrics || [];
            if (!metrics.length) return null;
            const sumCovered = metrics.reduce((s, m) => s + (m.covered || 0), 0);
            const sumTotal = metrics.reduce((s, m) => s + (m.total || 0), 0);
            const sumRate = sumTotal ? Math.round(sumCovered / sumTotal * 10000) / 100 : 0;
            const color = rateColor(sumRate);
            return {
                tooltip: {
                    trigger: 'item',
                    formatter: () => {
                        let html = '<b>' + mod + '</b> (' + (g.coverage[mod].count || '-') + ' 项)<br/>综合: '
                            + sumCovered + '/' + sumTotal + ' = ' + sumRate + '%';
                        metrics.forEach(m => {
                            html += '<br/>' + m.name + ': ' + m.covered + '/' + m.total + ' = ' + m.rate + '%';
                        });
                        return html;
                    },
                },
                series: [{
                    type: 'pie',
                    radius: ['58%', '78%'],
                    center: ['50%', '50%'],
                    avoidLabelOverlap: false,
                    label: {show: false},
                    labelLine: {show: false},
                    data: [
                        {value: sumCovered, name: '已覆盖', itemStyle: {color: color}},
                        {value: Math.max(sumTotal - sumCovered, 0), name: '未覆盖',
                         itemStyle: {color: '#e2e8f0'}},
                    ],
                    emphasis: {scale: false},
                }],
                graphic: [{
                    type: 'text', left: 'center', top: 'center',
                    style: {
                        text: sumRate + '%',
                        fontSize: 18, fontWeight: 'bold', fill: color,
                    },
                }, {
                    type: 'text', left: 'center', top: '62%',
                    style: {
                        text: sumCovered + '/' + sumTotal,
                        fontSize: 11, fill: '#94a3b8',
                    },
                }],
            };
        }

        function renderRings() {
            if (!chartReady.value) return;
            coverModules.forEach(mod => {
                const el = ringEls.value[mod];
                if (!el) return;
                if (!ringInstances[mod]) {
                    ringInstances[mod] = window.echarts.init(el);
                }
                const opt = ringOption(mod);
                if (opt) ringInstances[mod].setOption(opt, true);
            });
        }

        function resizeRings() {
            Object.values(ringInstances).forEach(inst => inst && inst.resize());
        }

        watch([coverData, granularity], () => {
            nextTick(renderRings);
        });

        // coverData 置空 → 详情区(v-if)销毁, 旧 echarts 实例绑定的是已移除 DOM,
        // 必须 dispose 并清空, 否则重建 DOM 后 setOption 作用在旧 canvas 上导致环形图丢失
        watch(coverData, (val) => {
            if (!val) {
                Object.values(ringInstances).forEach(inst => inst && inst.dispose());
                Object.keys(ringInstances).forEach(k => delete ringInstances[k]);
            }
        });

        const filteredDirs = computed(() => {
            const q = coverSearch.value.trim().toLowerCase();
            if (!q) return coverDirs.value;
            return coverDirs.value.filter(d =>
                (d.operator || '').toLowerCase().includes(q) ||
                (d.dir_name || '').toLowerCase().includes(q)
            );
        });

        function goBack() {
            window.location.href = '/static/operator-result.html';
        }

        // ---- 模块与指标 ----
        const coverModules = ['host', 'kernel', 'tiling', 'op'];

        function curGran() {
            const g = coverData.value && coverData.value.granularities;
            if (!g) return null;
            return g[granularity.value] || null;
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
        // 兼容两种 schema:
        //   dict (swin):  {file, coveredFuncCount, totalLines, items:[{name,dir,fullPath,lineStr,lineCount,covered_lines}]}
        //   list (lse):   [{file,path,func,line_begin,line_end,total_lines,cov_lines,cov_rate,covered_lines,uncovered_lines,dir}]
        const coveredFuncRows = computed(() => {
            const g = curGran();
            if (!g || !g.coveredFuncLineDetail) return [];
            const cfl = g.coveredFuncLineDetail;
            const linesMap = coveredUncoveredLines.value;
            const items = Array.isArray(cfl) ? cfl : (cfl.items || []);
            return items.map(it => {
                // 兼容字段名: name/func, fullPath/path, lineCount/cov_lines, lineStr 缺省时由 covered_lines 拼
                const name = it.name || it.func || '';
                const dir = it.dir || '';
                const fullPath = it.fullPath || it.path || '';
                const coveredLines = it.covered_lines || [];
                const lineCount = it.lineCount != null ? it.lineCount : (it.cov_lines != null ? it.cov_lines : coveredLines.length);
                const lineStr = it.lineStr || (coveredLines.length ? coveredLines.join(', ') : '');
                const la = linesMap[name] || {};
                return {
                    name, dir, fullPath, coveredLines, lineCount, lineStr,
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
            return '否';
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

        // ---- 选择目录 + 加载数据 (force=true 时即使同目录也重新拉取) ----
        async function selectCoverDir(d, force) {
            if (!force && currentCoverDir.value === d.dir_name && coverData.value) return;
            currentCoverDir.value = d.dir_name;
            detailLoading.value = true;
            coverData.value = null;
            uncoveredReasons.value = {};
            coveredUncoveredLines.value = {};
            expandedFunc.value = '';
            activePanels.value = ['uncovered'];
            try {
                const [covRes, anaRes] = await Promise.all([
                    fetch('/api/cover/' + encodeURIComponent(d.dir_name) + '/coverage?t=' + Date.now()).then(r => r.json()),
                    fetch('/api/cover/' + encodeURIComponent(d.dir_name) + '/analysis?t=' + Date.now()).then(r => r.json()).catch(() => null),
                ]);
                if (covRes && !covRes.error) {
                    coverData.value = covRes;
                    // 暂时固定 func 粒度 (file 粒度暂不展示)
                    granularity.value = 'func';
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

        // ---- 手动/定时刷新: 重读 ops_cov_report 目录 + 当前详情 ----
        const refreshing = ref(false);
        const lastRefresh = ref('');
        let pollTimer = null;
        const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 分钟

        function fmtTime(d) {
            const p = n => String(n).padStart(2, '0');
            return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
        }

        async function refreshAll(silent) {
            if (refreshing.value) return;
            refreshing.value = true;
            try {
                // 1. 重读目录列表
                const list = await (await fetch('/api/cover/dirs?t=' + Date.now())).json();
                coverDirs.value = Array.isArray(list) ? list : [];
                // 2. 当前选中目录若仍存在则强制重载, 否则选第一个
                let target = coverDirs.value.find(d => d.dir_name === currentCoverDir.value);
                if (!target) target = coverDirs.value[0] || null;
                if (target) {
                    await selectCoverDir(target, true);
                } else {
                    currentCoverDir.value = '';
                    coverData.value = null;
                }
                lastRefresh.value = fmtTime(new Date());
                if (!silent) ElMessage.success('已刷新');
                nextTick(renderRings);
            } catch (e) {
                if (!silent) ElMessage.error('刷新失败: ' + e.message);
            } finally {
                refreshing.value = false;
            }
        }

        function startPolling() {
            stopPolling();
            pollTimer = setInterval(() => refreshAll(true), POLL_INTERVAL_MS);
        }
        function stopPolling() {
            if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        }

        function escapeHtml(s) {
            return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        // ---- 初始化 ----
        onMounted(async () => {
            try {
                const list = await (await fetch('/api/cover/dirs')).json();
                coverDirs.value = Array.isArray(list) ? list : [];
                if (coverDirs.value.length) {
                    await selectCoverDir(coverDirs.value[0]);
                }
                lastRefresh.value = fmtTime(new Date());
                nextTick(renderRings);
            } catch (e) {
                ElMessage.error('加载目录列表失败: ' + e.message);
            }
            window.addEventListener('resize', resizeRings);
            startPolling();
        });

        onBeforeUnmount(() => {
            stopPolling();
            window.removeEventListener('resize', resizeRings);
            Object.values(ringInstances).forEach(inst => inst && inst.dispose());
            Object.keys(ringInstances).forEach(k => delete ringInstances[k]);
        });

        return {
            coverDirs, filteredDirs, coverSearch, currentCoverDir, detailLoading,
            coverData, granularity, activePanels,
            uncoveredReasons, coveredUncoveredLines, expandedFunc,
            coverModules, metricsOf, modCount, rateColor,
            opDomain, opDir, uncoveredRows, coveredFuncRows,
            ringEls, chartReady,
            refreshing, lastRefresh, refreshAll,
            selectCoverDir, goBack, funcDetails, toggleFunc, coverTypeOf, normalizeCover, coverTagType,
        };
    }
});
app.use(ElementPlus);
app.mount('#app');