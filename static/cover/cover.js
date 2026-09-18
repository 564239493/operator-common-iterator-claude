const {createApp, ref, computed, onMounted, nextTick} = Vue;
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
                            coverable: r.coverable || '',
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

        function coverTypeOf(cover) {
            if (cover.includes('✅') || cover.includes('是')) return 'yes';
            if (cover.includes('⚠') || cover.includes('可能')) return 'maybe';
            return 'no';
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
                    fetch('/api/cover/' + encodeURIComponent(d.dir_name) + '/coverage').then(r => r.json()),
                    fetch('/api/cover/' + encodeURIComponent(d.dir_name) + '/analysis').then(r => r.json()).catch(() => null),
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
        onMounted(async () => {
            try {
                const list = await (await fetch('/api/cover/dirs')).json();
                coverDirs.value = Array.isArray(list) ? list : [];
                if (coverDirs.value.length) {
                    await selectCoverDir(coverDirs.value[0]);
                }
            } catch (e) {
                ElMessage.error('加载目录列表失败: ' + e.message);
            }
        });

        return {
            coverDirs, filteredDirs, coverSearch, currentCoverDir, detailLoading,
            coverData, granularity, activePanels,
            uncoveredReasons, coveredUncoveredLines, expandedFunc,
            coverModules, metricsOf, modCount, rateColor,
            opDomain, opDir, uncoveredRows, coveredFuncRows,
            selectCoverDir, goBack, funcDetails, toggleFunc, coverTypeOf,
        };
    }
});
app.use(ElementPlus);
app.mount('#app');
