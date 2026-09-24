const {createApp, ref, computed, onMounted, onBeforeUnmount, nextTick, watch} = Vue;
const ElMessage = ElementPlus.ElMessage;

// 状态机 → 中文标签
const STATE_LABELS = {
    PLAN: '规划', INITIAL_EXTRACT: '首轮提取', EXTRACT: '约束提取',
    SUPPLEMENT: '约束补充', CONSTRAINT_CHECK: '检查修复', GENERATE: '用例生成',
    EXECUTE: '用例执行', GATE: '质量门禁', DIAGNOSE: '根因诊断',
    UPDATE_CONSTRAINTS: '增量更新', MIXED_FAILURE_REVIEW: '混合复核',
    NEEDS_HUMAN_EVIDENCE: '待人工证据', HUMAN_CHECKPOINT: '人工检查',
    AWAITING_HUMAN_CONSTRAINTS: '等待人工约束', ROLLBACK_TO_ITERATION: '回退轮次',
    SUCCESS: '成功', MAX_ITERATIONS: '轮次耗尽',
    STOP_GENERATOR_BUG: '生成器止损', STOP_EXECUTOR_BUG: '执行器止损',
    STOPPED_BY_USER: '用户终止', BLOCKED: '阻断'
};
const TERMINAL_STATES = new Set([
    'SUCCESS', 'MAX_ITERATIONS', 'STOP_GENERATOR_BUG',
    'STOP_EXECUTOR_BUG', 'STOPPED_BY_USER', 'BLOCKED'
]);

// 约束行状态 → el-tag type
function relStatusType(st) {
    if (st === 'pass') return 'success';
    if (st === 'fail') return 'danger';
    if (st === 'warn') return 'warning';
    return 'success';
}

function relStatusLabel(st) {
    if (st === 'pass') return '通过';
    if (st === 'fail') return '失败';
    if (st === 'warn') return '警告';
    return '通过';
}

const app = createApp({
    setup() {
        const DATA = ref({});
        const currentProduct = ref('');
        const runTasks = ref([]);
        const currentTaskDir = ref('');
        const currentIter = ref('');
        const iterList = ref([]);
        const taskLoading = ref(false);
        const queryLoading = ref(false);
        const activePanels = ref(['rel', 'inputs', 'outputs']);
        const progressPolling = ref(false);
        const history = ref([]);
        const editConstraints = ref([]);
        const editMode = ref(false);
        const submitLoading = ref(false);
        const timelineScroll = ref(null);
        const pflMap = ref({});
        // 原文定位弹框
        const docDialogVisible = ref(false);
        const docDialogLoading = ref(false);
        const docDialogError = ref('');
        const docDialogTitle = ref('原文定位');
        const docContent = ref('');
        const docSrcLines = ref([]);
        const docViewerRef = ref(null);
        // 轮次对比
        const prevIterDir = ref('');
        const diffLoading = ref(false);
        const diffError = ref('');
        const diffResult = ref(null);
        let progressTimer = null;

        const exprTypeOptions = [
            'presence_dependency', 'type_equality', 'shape_equality',
            'shape_value_dependency', 'value_dependency', 'cross_param_constraint',
            'range_dependency', 'format_dependency', 'custom'
        ];

        const productOptions = computed(() => DATA.value.product_support || []);

        // ---- 数据解包: 真实 constraints.json 双重按产品分组 ----
        function productPick(obj) {
            if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return obj;
            return (currentProduct.value && currentProduct.value in obj) ? obj[currentProduct.value] : obj;
        }

        function forProduct(section) {
            const raw = DATA.value[section];
            if (!raw) return (section === 'constraints_in_parameters') ? [] : {};
            if (Array.isArray(raw)) return raw;
            return productPick(raw);
        }

        function cellText(v) {
            if (v && typeof v === 'object' && !Array.isArray(v) && 'value' in v) v = v.value;
            if (v === null || v === undefined || v === '') return '';
            if (Array.isArray(v)) return v.join(', ');
            return String(v);
        }

        function boolText(v) {
            if (v && typeof v === 'object' && !Array.isArray(v) && 'value' in v) v = v.value;
            if (v === true) return '是';
            if (v === false) return '否';
            return (v === 'N/A') ? 'N/A' : '-';
        }

        // ---- 表格行数据 ----
        const statusFilter = ref('');
        const relRowsAll = computed(() => {
            const raw = forProduct('constraints_in_parameters') || [];
            const pfl = pflMap.value;
            return raw.map(c => {
                const id = c.id || '';
                const errors = id && pfl[id] ? pfl[id] : [];
                return {...c, _pflErrors: errors};
            });
        });
        const relRows = computed(() => {
            // 为每行附加轮次对比信息: 修改(表达式 diff) / 新增 / 删除
            let rows = relRowsAll.value;
            if (statusFilter.value === 'pass') rows = rows.filter(r => !r._pflErrors.length);
            else if (statusFilter.value === 'fail') rows = rows.filter(r => r._pflErrors.length);
            const res = diffResult.value;
            if (!res) return rows.map(r => ({...r, _diff: null}));
            // 上一轮 id -> 条目
            const prevMap = new Map();
            (res.modified || []).forEach(m => prevMap.set(m.id, m.prev));
            (res.removed || []).forEach(c => prevMap.set(c.id || '', c));
            const addedSet = new Set((res.added || []).map(c => c.id || ''));
            const modifiedMap = new Map((res.modified || []).map(m => [m.id, m]));
            let out = rows.map(r => {
                const id = r.id || '';
                const mod = modifiedMap.get(id);
                if (mod) return {...r,
                    _diff: {
                        type: 'modified',
                        html: mod.exprDiffHtml,
                        prevExpr: mod.prev.expr,
                        changedFields: mod.changedFields
                    }
                };
                if (addedSet.has(id)) return {...r, _diff: {type: 'added'}};
                return {...r, _diff: null};
            });
            // 删除的行: 上一轮有, 当前轮无, 追加到末尾
            (res.removed || []).forEach(c => {
                out.push({
                    id: c.id || '', expr: c.expr, expr_type: c.expr_type,
                    relation_params: c.relation_params || [], src_text: c.src_text,
                    _pflErrors: [], _diff: {type: 'removed', prevExpr: c.expr}
                });
            });
            // 轮次对比筛选: 仅显示指定变更类型
            const df = diffFilter.value;
            if (df) out = out.filter(r => r._diff && r._diff.type === df);
            return out;
        });
        const relCount = computed(() => relRowsAll.value.length);
        // 是否存在可对比的上一轮 (用于显示"变更"列)
        const hasDiff = computed(() => !!(diffResult.value && prevIterDir.value));
        // 轮次对比筛选: 点击 新增/删除/修改 胶囊只显示对应约束; null=全部
        const diffFilter = ref(null);

        function toggleDiffFilter(type) {
            diffFilter.value = (diffFilter.value === type) ? null : type;
        }

        function resetDiffFilter() {
            diffFilter.value = null;
        }

        const statusFilterOptions = [
            {text: '通过', value: 'pass'},
            {text: '不通过', value: 'fail'}
        ];

        function paramRows(section) {
            const obj = forProduct(section);
            return Object.keys(obj || {}).map(name => ({name, p: productPick(obj[name])}));
        }

        const inputRows = computed(() => paramRows('inputs'));
        const outputRows = computed(() => paramRows('outputs'));

        function relRowClass({row}) {
            return row.status === 'fail' ? 'row-fail' : '';
        }

        // ---- 约束编辑 ----
        function syncEditConstraints() {
            // 基于原始数据顺序同步, 保证编辑态与只读态顺序一致
            const pfl = pflMap.value;
            const raw = forProduct('constraints_in_parameters');
            if (Array.isArray(raw)) {
                editConstraints.value = raw.map(c => {
                    const id = c.id || '';
                    const errors = id && pfl[id] ? pfl[id] : [];
                    return {
                        id,
                        expr_type: c.expr_type || '',
                        expr: c.expr || '',
                        relation_params: [...(c.relation_params || [])],
                        src_text: c.src_text || '',
                        // 保留原始行号, 编辑态点击"原始文本"定位原文仍可用, 且提交时不丢
                        src_txt_line: Array.isArray(c.src_txt_line) ? [...c.src_txt_line] : [],
                        origin: c.origin || '',
                        status: c.status !== undefined ? c.status : 'pass',
                        _pflErrors: [...errors],
                        _newParam: ''
                    };
                });
            } else {
                editConstraints.value = [];
            }
        }

        function togglePanel(name) {
            const arr = activePanels.value;
            const i = arr.indexOf(name);
            if (i >= 0) arr.splice(i, 1);
            else arr.push(name);
        }

        function enterEditMode() {
            syncEditConstraints();
            editMode.value = true;
            if (!activePanels.value.includes('rel')) {
                activePanels.value = ['rel'];
            }
        }

        function cancelEdit() {
            editMode.value = false;
        }

        function goCover() {
            window.location.href = '/cover';
        }

        function addConstraint() {
            // 新增约束置于表格最上方，便于用户立即定位编辑
            editConstraints.value.unshift({
                id: '',
                expr_type: '',
                expr: '',
                relation_params: [],
                src_text: '',
                src_txt_line: [],
                origin: 'manual',
                status: null,
                _pflErrors: [],
                _newParam: ''
            });
        }

        function removeConstraint(idx) {
            editConstraints.value.splice(idx, 1);
        }

        function addParam(c) {
            const v = (c._newParam || '').trim();
            if (v && !c.relation_params.includes(v)) {
                c.relation_params.push(v);
            }
            c._newParam = '';
        }

        function removeParam(c, idx) {
            c.relation_params.splice(idx, 1);
        }

        // ---- 原文定位 ----
        const docLines = computed(() => docContent.value.split('\n'));
        // 高亮行号集合: src_txt_line 精确行号 (1-based)
        const docHitLines = computed(() => {
            const hits = new Set();
            if (!docContent.value) return hits;
            docSrcLines.value.forEach(n => {
                const idx = n - 1;
                if (idx >= 0 && idx < docLines.value.length) hits.add(idx);
            });
            return hits;
        });

        async function showSrcInDoc(row) {
            const task = runTasks.value.find(t => t.dir_name === currentTaskDir.value);
            if (!task) {
                ElMessage.warning('请先选择任务');
                return;
            }
            docDialogVisible.value = true;
            docDialogLoading.value = true;
            docDialogError.value = '';
            docDialogTitle.value = `原文定位 — ${row.id || ''} ${row.expr_type || ''}`.trim();
            // 精确行号: src_txt_line (1-based 数组)
            docSrcLines.value = Array.isArray(row.src_txt_line)
                ? row.src_txt_line.filter(n => Number.isInteger(n) && n > 0)
                : [];
            try {
                const res = await fetch(`/api/runs/${task.dir_name}/operator_doc`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                docContent.value = data.content || '';
            } catch (e) {
                docDialogError.value = `加载算子文档失败: ${e.message}`;
                docContent.value = '';
            } finally {
                docDialogLoading.value = false;
                nextTick(() => {
                    // 滚动到第一个高亮行
                    if (docViewerRef.value && docHitLines.value.size) {
                        const first = docViewerRef.value.querySelector('.doc-line-hit');
                        if (first) first.scrollIntoView({behavior: 'smooth', block: 'center'});
                    }
                });
            }
        }

        // 构建提交的单条约束对象: 保留 id 与 src_txt_line (轮次对比按 id 匹配依赖)
        function buildConstraintObj(c) {
            const o = {
                expr_type: c.expr_type,
                expr: c.expr,
                relation_params: c.relation_params,
                src_text: c.src_text,
                origin: c.origin || 'manual'
            };
            if (c.id) o.id = c.id;                       // 原位修改依赖 id, 不得丢
            if (Array.isArray(c.src_txt_line) && c.src_txt_line.length) o.src_txt_line = c.src_txt_line;
            if (c.status) o.status = c.status;
            return o;
        }

        async function submitConstraints() {
            const task = runTasks.value.find(t => t.dir_name === currentTaskDir.value);
            if (!task) {
                ElMessage.warning('请先选择任务');
                return;
            }
            submitLoading.value = true;
            try {
                // 构建编辑后的 constraints_in_parameters
                const base = (DATA.value.constraints_in_parameters || {});
                const newCnp = {};
                Object.keys(base).forEach(prod => {
                    if (prod === currentProduct.value) {
                        newCnp[prod] = editConstraints.value.map(c => buildConstraintObj(c));
                    } else {
                        newCnp[prod] = base[prod];
                    }
                });
                // 如果当前产品不在 base 中，也加上
                if (currentProduct.value && !(currentProduct.value in base)) {
                    newCnp[currentProduct.value] = editConstraints.value.map(c => buildConstraintObj(c));
                }
                const payload = {constraints: {constraints_in_parameters: newCnp}};
                const iterDir = iterDirOf(currentIter.value);
                const resp = await fetch(
                    '/api/runs/' + encodeURIComponent(currentTaskDir.value) + '/' + iterDir + '/constraints_update',
                    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}
                );
                const result = await resp.json();
                if (result.ok) {
                    ElMessage.success('已生成 ' + result.path);
                    editMode.value = false;
                    syncEditConstraints();
                } else {
                    ElMessage.error('生成失败: ' + (result.error || '未知错误'));
                }
            } catch (e) {
                ElMessage.error('请求失败: ' + e.message);
            } finally {
                submitLoading.value = false;
            }
        }

        // ---- 统计 ----
        // 约束通过/失败统计: 基于 PFL 错误信息 (与表格状态列一致)
        const statPass = computed(() => relRowsAll.value.filter(r => !r._pflErrors.length).length);
        const statFail = computed(() => relRowsAll.value.filter(r => r._pflErrors.length).length);
        const statWarn = computed(() => 0);

        // ---- 任务情况统计 (跨所有 runs) ----
        const taskStats = computed(() => {
            const tasks = runTasks.value;
            const total = tasks.length;
            let succ = 0, failed = 0, running = 0, passed = 0, failedCase = 0;
            tasks.forEach(t => {
                const s = t.state;
                if (s === 'SUCCESS') succ++;
                else if (s == null) { /* 未查询, 不计 */
                } else if (TERMINAL_STATES.has(s)) failed++;
                else running++;
                passed += (t.passed || 0);
                failedCase += (t.failed || 0);
            });
            return {total, succ, failed, running, passed, failedCase};
        });

        // ---- 执行历史 ----
        function stateLabel(state) {
            return STATE_LABELS[state] || state;
        }

        function isRunningState(state) {
            return !TERMINAL_STATES.has(state) && state != null;
        }

        function fmtTime(iso) {
            if (!iso) return '';
            const d = new Date(iso);
            if (isNaN(d.getTime())) return iso;
            const pad = (n) => String(n).padStart(2, '0');
            return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
                ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
        }

        function nodeClass(state, index) {
            const isLast = index === history.value.length - 1;
            if (isRunningState(state) && isLast) return 'running';
            if (state === 'SUCCESS') return 'success';
            if (TERMINAL_STATES.has(state)) return 'danger';
            if (index === 0) return 'primary';
            return 'info';
        }

        async function loadHistory(task) {
            try {
                const r = await (await fetch('/api/runs/' + encodeURIComponent(task.dir_name))).json();
                if (r && Array.isArray(r.history)) {
                    history.value = r.history;
                    return;
                }
            } catch (e) { /* 回退 */
            }
            history.value = [];
        }

        // ---- 时间线滚动 ----
        watch(history, () => {
            nextTick(() => {
                const el = timelineScroll.value;
                if (!el) return;
                if (el.scrollWidth > el.clientWidth) {
                    el.scrollLeft = el.scrollWidth;
                }
            });
        });

        // ---- 当前任务状态 (run_state.json 的 state 字段) ----
        const currentTaskState = computed(() => {
            const task = runTasks.value.find(t => t.dir_name === currentTaskDir.value);
            return task ? task.state : null;
        });
        const currentTaskStateType = computed(() => {
            const s = currentTaskState.value;
            if (s == null) return 'info';
            if (s === 'SUCCESS') return 'success';
            if (TERMINAL_STATES.has(s)) return 'danger';
            return 'primary';
        });

        // ---- 状态标签 ----
        const stateTag = computed(() => {
            const task = runTasks.value.find(t => t.dir_name === currentTaskDir.value);
            if (!task) return null;
            const state = task.state;
            if (state == null) return {text: '未查询', type: 'info', spinner: false, title: '点击按钮查询任务状态'};
            const label = STATE_LABELS[state] || state;
            const detail = (task.passed || 0) + ' 通过 / ' + (task.failed || 0) + ' 失败 / ' +
                (task.total || 0) + ' 总数' + (task.detail ? ' · ' + task.detail : '');
            if (TERMINAL_STATES.has(state)) {
                return {
                    text: label,
                    type: state === 'SUCCESS' ? 'success' : 'danger',
                    spinner: false,
                    title: state + ' · 已到达终态，轮询已停止\n' + detail
                };
            }
            return {
                text: label,
                type: 'primary',
                spinner: true,
                title: state + ' · 每 5 秒自动刷新\n' + detail
            };
        });

        // ---- 任务 / 迭代加载 ----
        async function loadRunTasks() {
            try {
                const list = await (await fetch('/api/runs')).json();
                return Array.isArray(list) ? list : [];
            } catch (e) {
                return [];
            }
        }

        function iterDirOf(iter) {
            return /^iter_\d+$/.test(iter) ? iter : 'iter_' + String(iter).padStart(3, '0');
        }

        async function loadIterList(task) {
            try {
                const r = await (await fetch('/api/runs/' + encodeURIComponent(task.dir_name) + '/iters')).json();
                if (r && Array.isArray(r.dirs)) return r.dirs;
            } catch (e) { /* 回退 */
            }
            return [String(task.current_iteration || 1)];
        }

        async function applyTaskData(task, iter) {
            currentIter.value = iter;
            let loaded = null;
            try {
                const c = await (await fetch('/api/runs/' + encodeURIComponent(task.dir_name) +
                    '/' + iterDirOf(iter) + '/constraints')).json();
                if (c && !c.error) loaded = c;
            } catch (e) { /* API 不可用 */
            }
            if (loaded) {
                DATA.value = loaded;
                currentProduct.value = (loaded.product_support || [])[0] || '';
            }
            editMode.value = false;
            syncEditConstraints();
            await loadAnalysis(task, iter);
            await loadDiff(task);
        }

        async function loadAnalysis(task, iter) {
            try {
                const r = await (await fetch('/api/runs/' + encodeURIComponent(task.dir_name) +
                    '/' + iterDirOf(iter) + '/analysis')).json();
                if (r && !r.error && Array.isArray(r.param_failure_locations)) {
                    const map = {};
                    r.param_failure_locations.forEach(pfl => {
                        (pfl.target_id || []).forEach(tid => {
                            if (!map[tid]) map[tid] = [];
                            map[tid].push(pfl);
                        });
                    });
                    pflMap.value = map;
                    return;
                }
            } catch (e) { /* 回退 */
            }
            pflMap.value = {};
        }

        // 根据当前任务状态同步轮询: 非终止态启动, 终止态停止
        function syncPollingByCurrentState() {
            const s = currentTaskState.value;
            if (s && !TERMINAL_STATES.has(s)) {
                startProgressPolling();
            } else {
                stopProgressPolling();
            }
        }

        async function onTaskChange(dir) {
            const task = runTasks.value.find(t => t.dir_name === dir);
            if (!task) return;
            currentTaskDir.value = dir;
            taskLoading.value = true;
            try {
                const iters = await loadIterList(task);
                iterList.value = iters;
                const defaultIter = iters.length ? iters[iters.length - 1] : String(task.current_iteration || 1);
                await applyTaskData(task, defaultIter);
                await loadHistory(task);
                syncPollingByCurrentState();
            } finally {
                taskLoading.value = false;
            }
        }

        async function onIterChange(iter) {
            const task = runTasks.value.find(t => t.dir_name === currentTaskDir.value);
            if (!task) return;
            await applyTaskData(task, iter);
        }

        // ---- 轮次对比: 当前轮 vs 上一轮 ----
        function flattenCnp(cnp) {
            // 把 constraints_in_parameters (dict-by-product 或 array) 拍平为条目数组
            const items = [];
            if (Array.isArray(cnp)) {
                cnp.forEach(c => items.push(c));
            } else if (cnp && typeof cnp === 'object') {
                Object.keys(cnp).forEach(prod => {
                    const arr = cnp[prod];
                    if (Array.isArray(arr)) arr.forEach(c => items.push(c));
                });
            }
            return items;
        }

        function buildIdMap(items) {
            const map = new Map();
            items.forEach((c, i) => {
                const id = c.id || `(__no_id_${i})`;
                // 同 id 多条时保留第一条 (真实数据 id 唯一)
                if (!map.has(id)) map.set(id, c);
            });
            return map;
        }

        function escHtml(s) {
            return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        // 基于公共前缀/后缀的轻量 diff: 在 prev→cur 间用 del/ins 标注变化
        function exprDiffHtml(prev, cur) {
            const a = String(prev == null ? '' : prev);
            const b = String(cur == null ? '' : cur);
            if (a === b) return `<span>${escHtml(a)}</span>`;
            // 公共前缀
            let start = 0;
            while (start < a.length && start < b.length && a[start] === b[start]) start++;
            // 公共后缀 (不与前缀重叠)
            let endA = a.length, endB = b.length;
            while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
                endA--;
                endB--;
            }
            const prefix = a.slice(0, start);
            const delPart = a.slice(start, endA);
            const insPart = b.slice(start, endB);
            const suffix = a.slice(endA);
            let html = '';
            if (prefix) html += escHtml(prefix);
            if (delPart) html += `<del>${escHtml(delPart)}</del>`;
            if (insPart) html += `<ins>${escHtml(insPart)}</ins>`;
            if (suffix) html += escHtml(suffix);
            return html;
        }

        function computeDiff(prevItems, curItems) {
            const prevMap = buildIdMap(prevItems);
            const curMap = buildIdMap(curItems);
            const added = [];
            const removed = [];
            const modified = [];
            // 新增: 当前轮有, 上一轮无
            curMap.forEach((c, id) => {
                if (!prevMap.has(id)) added.push(c);
            });
            // 删除: 上一轮有, 当前轮无
            prevMap.forEach((c, id) => {
                if (!curMap.has(id)) removed.push(c);
            });
            // 修改: 两边都有, 字段有差异
            prevMap.forEach((pc, id) => {
                const cc = curMap.get(id);
                if (!cc) return;
                const fields = ['expr', 'expr_type', 'relation_params', 'src_text', 'origin'];
                const changedFields = fields.filter(f => JSON.stringify(pc[f]) !== JSON.stringify(cc[f]));
                if (changedFields.length) {
                    modified.push({
                        id, prev: pc, cur: cc, changedFields,
                        exprDiffHtml: exprDiffHtml(pc.expr, cc.expr)
                    });
                }
            });
            // 行: 修改 + 新增 + 删除, 按 id 排序
            const rows = [];
            modified.forEach(m => rows.push({
                id: m.id, change: 'modified', prev: m.prev, cur: m.cur,
                changedFields: m.changedFields, exprDiffHtml: m.exprDiffHtml
            }));
            added.forEach(c => rows.push({id: c.id || '', change: 'added', prev: null, cur: c}));
            removed.forEach(c => rows.push({id: c.id || '', change: 'removed', prev: c, cur: null}));
            rows.sort((x, y) => String(x.id).localeCompare(String(y.id)));
            return {prevCount: prevItems.length, curCount: curItems.length, added, removed, modified, rows};
        }

        async function loadDiff(task) {
            diffLoading.value = true;
            diffError.value = '';
            diffResult.value = null;
            diffFilter.value = null;
            const cur = currentIter.value;
            const idx = iterList.value.indexOf(cur);
            if (idx <= 0) {
                // 第一轮, 无上一轮
                prevIterDir.value = '';
                diffLoading.value = false;
                return;
            }
            const prev = iterList.value[idx - 1];
            prevIterDir.value = prev;
            try {
                const r = await (await fetch('/api/runs/' + encodeURIComponent(task.dir_name) +
                    '/' + iterDirOf(prev) + '/constraints')).json();
                if (r && r.error) throw new Error(r.error);
                const prevItems = flattenCnp(r && r.constraints_in_parameters);
                const curItems = flattenCnp(DATA.value.constraints_in_parameters);
                diffResult.value = computeDiff(prevItems, curItems);
            } catch (e) {
                diffError.value = '加载上一轮约束失败: ' + e.message;
            } finally {
                diffLoading.value = false;
            }
        }

        // ---- 轮询 ----
        function startProgressPolling() {
            if (progressTimer) return;
            progressPolling.value = true;
            progressTimer = setInterval(refreshProgress, 5000);
        }

        function stopProgressPolling() {
            if (progressTimer) {
                clearInterval(progressTimer);
                progressTimer = null;
            }
            progressPolling.value = false;
        }

        async function refreshProgress() {
            try {
                runTasks.value = await loadRunTasks();
                await Promise.all(runTasks.value.map(async (t) => {
                    try {
                        const er = await (await fetch('/api/runs/' + encodeURIComponent(t.dir_name) +
                            '/' + iterDirOf(t.current_iteration) + '/execution_result')).json();
                        t.passed = er.passed || 0;
                        t.failed = er.failed || 0;
                        t.total = er.total || (t.passed + t.failed);
                        t.detail = er.engine_error || (er.status === 'generate' ? '已生成执行产物，未连远端' : '');
                    } catch (e) {
                        t.passed = 0;
                        t.failed = 0;
                        t.total = 0;
                        t.detail = '';
                    }
                }));
                // 刷新当前任务的执行历史
                const currentTask = runTasks.value.find(t => t.dir_name === currentTaskDir.value);
                if (currentTask) {
                    await loadHistory(currentTask);
                    // 非编辑态: 同步刷新当前轮次表格数据(约束/输入/输出/PFL错误)
                    if (!editMode.value && currentIter.value) {
                        try {
                            const c = await (await fetch('/api/runs/' + encodeURIComponent(currentTask.dir_name) +
                                '/' + iterDirOf(currentIter.value) + '/constraints')).json();
                            if (c && !c.error) {
                                DATA.value = c;
                            }
                            await loadAnalysis(currentTask, currentIter.value);
                            await loadDiff(currentTask);
                        } catch (e) { /* 单次表格刷新失败不中断 */
                        }
                    }
                }
                // 当前任务进入终止态则停止轮询, 非终止态确保在跑
                syncPollingByCurrentState();
            } catch (e) { /* 单次失败不中断 */
            }
        }

        async function queryProgress() {
            queryLoading.value = true;
            try {
                if (!runTasks.value.length) runTasks.value = await loadRunTasks();
                if (!runTasks.value.length) {
                    // API 不可用: 无任务可查
                    return;
                }
                await refreshProgress();
                // refreshProgress 末尾已按当前任务状态同步轮询, 此处补查列表级启停
                if (runTasks.value.some(r => !TERMINAL_STATES.has(r.state))) {
                    startProgressPolling();
                }
            } finally {
                queryLoading.value = false;
            }
        }

        // ---- 初始化 ----
        onMounted(async () => {
            currentProduct.value = (DATA.value.product_support || [])[0] || '';
            runTasks.value = await loadRunTasks();
            if (runTasks.value.length) {
                // 深链: ?run=<dir>&iter=<iter> 定位到指定 run/iter (raise_dashboard.py 拉起时使用)
                const qp = new URLSearchParams(window.location.search);
                const qRun = qp.get('run');
                const qIter = qp.get('iter');
                const match = qRun ? runTasks.value.find(t => t.dir_name === qRun) : null;
                const first = match || runTasks.value[0];
                currentTaskDir.value = first.dir_name;
                const iters = await loadIterList(first);
                iterList.value = iters;
                let defaultIter = iters.length ? iters[iters.length - 1] : String(first.current_iteration || 1);
                if (qIter && iters.includes(qIter)) defaultIter = qIter;
                await applyTaskData(first, defaultIter);
                await loadHistory(first);
                // 当前任务非终止态: 自动每 5 秒轮询
                syncPollingByCurrentState();
            }
        });
        onBeforeUnmount(() => stopProgressPolling());

        return {
            currentProduct, productOptions,
            runTasks, currentTaskDir, iterList, currentIter, taskLoading, queryLoading,
            activePanels, stateTag, progressPolling, history,
            currentTaskState, currentTaskStateType,
            stateLabel, isRunningState, fmtTime, nodeClass,
            relRows, relCount, hasDiff, relRowClass, relStatusType, relStatusLabel,
            statusFilterOptions,
            inputRows, outputRows, cellText, boolText,
            statPass, statFail, statWarn, taskStats,
            onTaskChange, onIterChange, queryProgress, goCover,
            editConstraints, editMode, submitLoading, exprTypeOptions,
            enterEditMode, cancelEdit,
            addConstraint, removeConstraint, addParam, removeParam,
            syncEditConstraints, submitConstraints,
            togglePanel,
            timelineScroll,
            docDialogVisible, docDialogLoading, docDialogError, docDialogTitle,
            docLines, docHitLines, docViewerRef, showSrcInDoc,
            prevIterDir, diffLoading, diffError, diffResult,
            diffFilter, toggleDiffFilter, resetDiffFilter,
        };
    }
});
app.use(ElementPlus);
app.mount('#app');