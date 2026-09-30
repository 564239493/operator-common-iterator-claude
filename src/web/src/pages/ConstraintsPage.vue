<script>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { marked } from 'marked';
import { useTask } from '../composables/useTask';
import TaskPicker from '../components/task/TaskPicker.vue';


// 状态机 → 中文标签（与 run_state.py 的 ALLOWED_STATES 对齐，无幽灵态）
const STATE_LABELS = {
    PLAN: '规划', EXTRACT: '约束提取',
    GENERATE: '用例生成',
    EXECUTE: '用例执行', GATE: '质量门禁', DIAGNOSE: '根因诊断',
    UPDATE_CONSTRAINTS: '增量更新', MIXED_FAILURE_REVIEW: '混合复核',
    NEEDS_HUMAN_EVIDENCE: '待人工证据', HUMAN_CHECKPOINT: '人工检查',
    AWAITING_HUMAN_CONSTRAINTS: '等待人工约束',
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
    if (st === 'pass') return 'info';
    if (st === 'fail') return 'danger';
    if (st === 'warn') return 'warning';
    return 'info';
}

function relStatusLabel(st) {
    if (st === 'pass') return '未关联失败';
    if (st === 'fail') return '失败';
    if (st === 'warn') return '警告';
    return '通过';
}

export default {
    components: { TaskPicker },
    setup() {
        const DATA = ref({});
        const currentProduct = ref('');
        // 任务身份来自全站共享选择（算子名 × 测试运行），不再本页拉任务列表
        const { runs, selectedRunId, selectedRun, waitForReady } = useTask();
        // 当前任务执行进度：本页 5s 轮询只取当前任务（原实现扇出全部任务，但 UI 仅展示当前任务数字）
        const progressExtra = ref(null);
        const currentTask = computed(() => {
            if (!selectedRunId.value) return null;
            const r = selectedRun.value;
            const p = progressExtra.value || {};
            return {
                dir_name: selectedRunId.value,
                state: r ? (r.state ?? null) : null,
                current_iteration: r ? r.current_iteration : undefined,
                passed: p.passed || 0, failed: p.failed || 0, total: p.total || 0, detail: p.detail || ''
            };
        });
        const currentIter = ref('');
        const iterList = ref([]);
        const taskLoading = ref(false);
        const queryLoading = ref(false);
        const activePanels = ref(['rel', 'inputs', 'outputs']);
        const progressPolling = ref(false);
        const history = ref([]);
        // 任务状态时间线默认折叠（占位大、非主流程信息）
        const timelineCollapsed = ref(true);
        // 原文弹框双模式：raw=带行号原文，rendered=markdown 渲染（均支持命中行定位）
        const docMode = ref('rendered');
        // 表格固定表头：el-table max-height（跟随窗口高度），表头原生吸顶
        const tableMaxHeight = ref(420);
        const syncTableMaxHeight = () => { tableMaxHeight.value = Math.max(320, window.innerHeight - 430); };
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
        const docMdRef = ref(null);
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

        const productOptions = computed(() => DATA.value.product_support?.length ? DATA.value.product_support : ['未分组']);

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
            const value = forProduct('constraints_in_parameters'); const raw = Array.isArray(value) ? value : [];
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
            {text: '未关联失败', value: 'pass'},
            {text: '关联失败', value: 'fail'}
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
                        ...c,
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
            // 任务上下文由全站共享选择携带，覆盖率页直达该任务内嵌的 runs/<run>/ops_cov_report/
            window.location.href = '/coverage';
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
        // markdown 渲染视图（GFM 表格支持；文档为项目内受信快照）。
        // 命中行锚点：在源文本命中行首注入零宽注释，渲染后替换为高亮 span —— 渲染视图也能定位。
        const docHtml = computed(() => {
            if (!docContent.value) return '';
            try {
                const hits = docHitLines.value;
                const marked2 = hits.size
                    ? docContent.value.split('\n')
                        .map((line, li) => (hits.has(li) ? '<!--WBHIT-->' + line : line))
                        .join('\n')
                    : docContent.value;
                return marked.parse(marked2, { gfm: true, breaks: false })
                    .replace(/<!--WBHIT-->/g, '<span class="doc-md-hit"></span>')
                    .replace(/&lt;!--WBHIT--&gt;/g, '');
            } catch (e) {
                return '';
            }
        });
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
            const task = currentTask.value;
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
            // 有定位行号 → 渲染视图也带锚点高亮；两种模式均自动滚动到首个命中行
            docMode.value = 'rendered';
            try {
                const res = await fetch(`/api/review/runs/${task.dir_name}/operator_doc?source=${encodeURIComponent(row.origin || 'doc')}`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                docContent.value = data.content || '';
            } catch (e) {
                docDialogError.value = `加载算子文档失败: ${e.message}`;
                docContent.value = '';
            } finally {
                docDialogLoading.value = false;
                nextTick(() => {
                    // 滚动到第一个命中锚点（渲染视图 .doc-md-hit / 原文视图 .doc-line-hit）
                    if (docHitLines.value.size) {
                        const first = docMode.value === 'rendered'
                            ? docMdRef.value?.querySelector('.doc-md-hit')
                            : docViewerRef.value?.querySelector('.doc-line-hit');
                        if (first) first.scrollIntoView({behavior: 'smooth', block: 'center'});
                    }
                });
            }
        }

        // 构建提交的单条约束对象: 保留 id 与 src_txt_line (轮次对比按 id 匹配依赖)
        function buildConstraintObj(c) {
            const {_pflErrors, _newParam, _diff, ...preserved} = c;
            const o = {
                ...preserved,
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
            const task = currentTask.value;
            if (!task) {
                ElMessage.warning('请先选择任务');
                return;
            }
            submitLoading.value = true;
            try {
                // 构建编辑后的 constraints_in_parameters
                const original = DATA.value.constraints_in_parameters || {};
                const base = Array.isArray(original) ? {[currentProduct.value || '未分组']: original} : original;
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
                const payload = {constraints: {constraints_in_parameters: Array.isArray(original) ? newCnp[currentProduct.value || '未分组'] : newCnp}, base_sha256: DATA.value._review?.base_sha256, copy_sha256: DATA.value._review?.copy_sha256};
                const iterDir = iterDirOf(currentIter.value);
                const resp = await fetch(
                    '/api/review/runs/' + encodeURIComponent(task.dir_name) + '/' + iterDir + '/constraints_update',
                    {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Review-Token': DATA.value._review?.token || ''}, body: JSON.stringify(payload)}
                );
                const result = await resp.json();
                if (result.ok) {
                    ElMessage.success('已保存修改副本，尚未启动新一轮执行');
                    DATA.value._review.copy_sha256 = result.sha256;
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

        // ---- 任务情况统计 (跨所有 runs, 共享任务列表) ----
        const taskStats = computed(() => {
            const tasks = runs.value.filter(r => !r.parse_error);
            const total = tasks.length;
            let succ = 0, failed = 0, running = 0;
            tasks.forEach(t => {
                const s = t.state;
                if (s === 'SUCCESS') succ++;
                else if (s == null) { /* 未查询, 不计 */
                } else if (TERMINAL_STATES.has(s)) failed++;
                else running++;
            });
            return {total, succ, failed, running};
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
                const r = await (await fetch('/api/review/runs/' + encodeURIComponent(task.dir_name))).json();
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
        const currentTaskState = computed(() => currentTask.value ? currentTask.value.state : null);
        const currentTaskStateType = computed(() => {
            const s = currentTaskState.value;
            if (s == null) return 'info';
            if (s === 'SUCCESS') return 'success';
            if (TERMINAL_STATES.has(s)) return 'danger';
            return 'primary';
        });

        // ---- 状态标签 ----
        const stateTag = computed(() => {
            const task = currentTask.value;
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
        function iterDirOf(iter) {
            return /^iter_\d+$/.test(iter) ? iter : 'iter_' + String(iter).padStart(3, '0');
        }

        async function loadIterList(task) {
            try {
                const r = await (await fetch('/api/review/runs/' + encodeURIComponent(task.dir_name) + '/iters')).json();
                if (r && Array.isArray(r.dirs)) return r.dirs;
            } catch (e) { /* 回退 */
            }
            return [String(task.current_iteration || 1)];
        }

        async function applyTaskData(task, iter) {
            currentIter.value = iter;
            let loaded = null;
            try {
                const c = await (await fetch('/api/review/runs/' + encodeURIComponent(task.dir_name) +
                    '/' + iterDirOf(iter) + '/constraints')).json();
                if (c && !c.error) loaded = c;
            } catch (e) { /* API 不可用 */
            }
            DATA.value = {}; currentProduct.value = '';
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
                const r = await (await fetch('/api/review/runs/' + encodeURIComponent(task.dir_name) +
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

        // 任务切换/装载：iters → 默认最新轮（可被 iterHint 覆盖）→ 约束/分析/对比 → 历史 → 按状态轮询
        async function loadTask(iterHint) {
            const task = currentTask.value;
            if (!task) return;
            taskLoading.value = true;
            try {
                DATA.value = {}; pflMap.value = {}; diffResult.value = null;
                const iters = await loadIterList(task);
                iterList.value = iters;
                let defaultIter = iters.length ? iters[iters.length - 1] : String(task.current_iteration || 1);
                if (iterHint && iters.includes(iterHint)) defaultIter = iterHint;
                await applyTaskData(task, defaultIter);
                await loadHistory(task);
                syncPollingByCurrentState();
            } finally {
                taskLoading.value = false;
            }
        }

        // 共享选择变化 → 装载新任务。immediate 让首次装载也走同一条路
        // （持久化选择立即装载；列表就绪后校验性自动选择同样触发），
        // 编辑态选择器已禁用，防御外部种子；深链 ?iter= 仅首轮生效。
        const qIterDeepLink = new URLSearchParams(window.location.search).get('iter');
        let firstTaskLoad = true;
        watch(selectedRunId, async (dir, prev) => {
            if (!dir) return;
            if (prev && editMode.value) return;
            const hint = firstTaskLoad ? (qIterDeepLink || undefined) : undefined;
            firstTaskLoad = false;
            await loadTask(hint);
        }, { immediate: true });
        // 共享列表刷新后任务状态变化（如外部续跑）→ 同步轮询启停
        watch(() => selectedRun.value?.state, () => syncPollingByCurrentState());

        async function onIterChange(iter) {
            const task = currentTask.value;
            if (!task) return;
            await applyTaskData(task, iter);
        }

        watch(currentProduct, () => { const task = currentTask.value; if (task) loadDiff(task); });
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
                const fields = ['expr', 'expr_type', 'relation_params', 'src_text', 'src_txt_line', 'origin'];
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
                const r = await (await fetch('/api/review/runs/' + encodeURIComponent(task.dir_name) +
                    '/' + iterDirOf(prev) + '/constraints')).json();
                if (r && r.error) throw new Error(r.error);
                const pick = c => Array.isArray(c) ? c : (c?.[currentProduct.value] || []);
                const prevItems = pick(r?.constraints_in_parameters);
                const curItems = pick(DATA.value.constraints_in_parameters);
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

        // 注：任务列表由全站共享状态 15s 轮询（useTask），新任务自动进入选择器、
        // 状态变化经 watch 同步轮询启停，本页不再维护 30s 慢速列表刷新。

        async function refreshProgress() {
            try {
                const task = currentTask.value;
                if (task && selectedRun.value) {
                    // 执行进度：只取当前任务当前轮（UI 仅展示当前任务数字）
                    try {
                        const er = await (await fetch('/api/review/runs/' + encodeURIComponent(task.dir_name) +
                            '/' + iterDirOf(selectedRun.value.current_iteration) + '/execution_result')).json();
                        progressExtra.value = {
                            passed: er.passed || 0,
                            failed: er.failed || 0,
                            total: er.total || ((er.passed || 0) + (er.failed || 0)),
                            detail: er.engine_error || (er.status === 'generate' ? '已生成执行产物，未连远端' : '')
                        };
                    } catch (e) {
                        progressExtra.value = null;
                    }
                    // 刷新当前任务的执行历史
                    await loadHistory(task);
                    // 非编辑态: 同步刷新当前轮次表格数据(约束/输入/输出/PFL错误)
                    if (!editMode.value && currentIter.value) {
                        try {
                            const c = await (await fetch('/api/review/runs/' + encodeURIComponent(task.dir_name) +
                                '/' + iterDirOf(currentIter.value) + '/constraints')).json();
                            if (c && !c.error) {
                                DATA.value = c;
                            }
                            await loadAnalysis(task, currentIter.value);
                            await loadDiff(task);
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
                await waitForReady();
                if (!selectedRunId.value) {
                    // API 不可用或无任务可查
                    return;
                }
                await refreshProgress();
            } finally {
                queryLoading.value = false;
            }
        }

        // ---- 初始化 ----
        // 任务装载由上方 watch(selectedRunId, {immediate}) 统一负责
        onMounted(() => { syncTableMaxHeight(); window.addEventListener('resize', syncTableMaxHeight); });
        onBeforeUnmount(() => { stopProgressPolling(); window.removeEventListener('resize', syncTableMaxHeight); });

        return {
            currentProduct, productOptions,
            selectedRunId, iterList, currentIter, taskLoading, queryLoading,
            activePanels, stateTag, progressPolling, history,
            timelineCollapsed, docMode, docHtml, docMdRef, tableMaxHeight,
            currentTaskState, currentTaskStateType,
            stateLabel, isRunningState, fmtTime, nodeClass,
            relRows, relCount, hasDiff, relRowClass, relStatusType, relStatusLabel,
            statusFilterOptions,
            inputRows, outputRows, cellText, boolText,
            statPass, statFail, statWarn, taskStats,
            onIterChange, queryProgress, goCover,
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
};
</script>
<template>
<div class="legacy-review">
    <!-- 顶栏: 标题 + 任务情况汇总 + 覆盖率展示按钮 -->
    <div class="topbar">
        <div class="topbar-right">
            <div class="topbar-title">算子自主测试智能体-约束审核工具</div>
            <div class="divider-v"></div>
            <template v-if="taskStats.total">
                <div class="stat-item stat-total"><span class="stat-num">{{ taskStats.total }}</span> 任务</div>
                <div class="stat-item stat-succ"><span class="stat-num">{{ taskStats.succ }}</span> 成功</div>
                <div class="stat-item stat-bad"><span class="stat-num">{{ taskStats.failed }}</span> 失败/阻断</div>
                <div class="stat-item stat-running"><span class="stat-num">{{ taskStats.running }}</span> 运行中</div>
            </template>
        </div>
        <div><a :href="selectedRunId ? '/run/' + encodeURIComponent(selectedRunId) : '/'">返回工作台</a> <el-button @click="goCover">覆盖率展示</el-button></div>
    </div>

    <!-- 副信息栏: 任务/轮次/产品系列 一组选择器 + 约束统计 -->
    <div class="info-bar">
        <div class="info-left">
            <span class="field-label">算子任务</span>
            <TaskPicker :disabled="editMode" />
            <span class="field-label">轮次</span>
            <el-select v-model="currentIter" placeholder="迭代" style="width:120px" :disabled="!selectedRunId || editMode"
                       @change="onIterChange">
                <el-option v-for="d in iterList" :key="d" :label="d" :value="d"></el-option>
            </el-select>
            <span class="field-label">产品系列</span>
            <el-select v-model="currentProduct" :disabled="editMode" style="width:280px" filterable @change="syncEditConstraints">
                <el-option v-for="p in productOptions" :key="p" :label="p" :value="p"></el-option>
            </el-select>
            <el-tag v-if="currentTaskState" :type="currentTaskStateType" effect="light" class="state-tag"
                    size="default">
                <span v-if="isRunningState(currentTaskState)" class="spinner"></span>{{ stateLabel(currentTaskState) }}
            </el-tag>
        </div>
        <div class="info-right">
            <div class="stat-item stat-pass"><span class="stat-num">{{ statPass }}</span> 未关联失败</div>
            <div class="stat-item stat-fail"><span class="stat-num">{{ statFail }}</span> 关联失败</div>
        </div>
    </div>

    <!-- 时间线: 任务状态迁移轨迹 (run_state.history), 可折叠 -->
    <div class="timeline-bar">
        <div class="timeline-head">
            <span class="timeline-title">任务状态轨迹 · 共 {{ history.length }} 次状态迁移
                <em>来自 run_state.json 的 history：本次任务从规划到终态经历过的每个阶段（含重试、止损、门禁、诊断）</em>
            </span>
            <button v-if="history.length" class="timeline-toggle" @click="timelineCollapsed = !timelineCollapsed">
                {{ timelineCollapsed ? '展开 ▾' : '收起 ▴' }}
            </button>
        </div>
        <div class="history-wrap" v-show="!timelineCollapsed">
            <span v-if="!history.length" class="history-empty">暂无记录</span>
            <div v-else class="timeline-scroll" ref="timelineScroll">
                <div class="op-timeline-h">
                    <div v-for="(h, i) in history" :key="i" class="timeline-node" :class="nodeClass(h.state, i)">
                        <div class="node-dot"></div>
                        <div class="node-label">{{ stateLabel(h.state) }}</div>
                        <div class="node-time">{{ fmtTime(h.at) }}</div>
                        <div v-if="i < history.length - 1" class="node-line"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="constraint-toolbar">
        <span class="toolbar-hint">
            {{ editMode ? '编辑模式 — 保存人工修改副本；不会直接启动执行' : '浏览模式 · 未关联失败不等于验证通过'}}
        </span>
        <div style="display:flex;gap:8px">
            <template v-if="!editMode">
                <el-button size="small" type="primary" plain @click="enterEditMode">编辑</el-button>
            </template>
            <template v-else>
                <el-button size="small" type="primary" plain @click="addConstraint">＋ 新增
                </el-button>
                <el-button size="small" plain @click="cancelEdit">取消</el-button>
                <el-button size="small" type="success" :loading="submitLoading"
                           @click="submitConstraints">保存修改副本
                </el-button>
            </template>
        </div>
    </div>

    <p class="save-note">修改仅保存为本轮 constraints_copy.json；需由业务会话校验并接入下一轮。原约束与任务状态不会被覆盖。已有副本会在下一次成功提交时更新。</p>
    <!-- 主体 -->
    <div class="main">
        <!-- 主体卡片: 约束 + 输入 + 输出 (div 替代 el-collapse) -->
        <div class="cards">
            <!-- 参数间约束 -->
            <div class="card" :class="{expanded: activePanels.includes('rel')}" data-panel="rel">
                <div class="card-header" @click="togglePanel('rel')">
                    <div class="title-row">
                        <div class="card-title">
                            <div class="icon icon-constraint">🔗</div>
                            参数间约束 constraints_in_parameters
                        </div>
                        <div class="card-count">{{ relCount }} 项约束</div>
                    </div>
                    <div class="card-toggle" :class="{open: activePanels.includes('rel')}">▾</div>
                </div>
                <div class="card-body" v-show="activePanels.includes('rel')">
                    <div class="constraint-area">
                        <!-- 轮次对比: 约束数量差异统计 -->
                        <div v-if="hasDiff" class="diff-stats" @click.stop>
                            <span class="diff-stats-title">轮次对比（{{ prevIterDir }} → {{ currentIter }}）</span>
                            <span class="diff-stat-chip total">
                                上一轮 <b>{{ diffResult.prevCount }}</b>
                                <span class="arrow">→</span>
                                当前轮 <b>{{ diffResult.curCount }}</b>
                            </span>
                            <span class="diff-stat-chip added clickable" :class="{active: diffFilter==='added'}"
                                  @click="toggleDiffFilter('added')">新增 <b>{{ diffResult.added.length }}</b></span>
                            <span class="diff-stat-chip removed clickable" :class="{active: diffFilter==='removed'}"
                                  @click="toggleDiffFilter('removed')">删除 <b>{{ diffResult.removed.length
                                }}</b></span>
                            <span class="diff-stat-chip modified clickable" :class="{active: diffFilter==='modified'}"
                                  @click="toggleDiffFilter('modified')">修改 <b>{{ diffResult.modified.length
                                }}</b></span>
                            <span v-if="!diffResult.rows.length && !diffFilter"
                                  class="diff-stat-chip none">两轮约束完全一致</span>
                            <button v-if="diffFilter" class="diff-reset-btn" @click="resetDiffFilter">重置</button>
                        </div>
                        <el-table :data="editMode ? editConstraints : relRows" class="op-table" :max-height="tableMaxHeight">
                            <el-table-column type="index" label="#" width="40" align="center"></el-table-column>

                            <!-- 类型: 始终只读 -->
                            <el-table-column label="类型" width="180">
                                <template #default="{ row }">
                                    <el-select v-if="editMode" v-model="row.expr_type" filterable allow-create default-first-option placeholder="约束类型"><el-option v-for="type in exprTypeOptions" :key="type" :label="type" :value="type" /></el-select><span v-else class="c-type-tag">{{ row.expr_type || '-' }}</span>
                                </template>
                            </el-table-column>

                            <!-- 表达式: 只读文本 / 编辑 textarea (唯一可编辑列); 修改时显示 diff -->
                            <el-table-column label="表达式 Expr" min-width="320">
                                <template #default="{ row }">
                                    <div v-if="editMode">
                                        <el-input v-model="row.expr" type="textarea" :rows="2" size="small"
                                                  class="c-cell-edit"></el-input>
                                    </div>
                                    <template v-else>
                                        <!-- 修改: diff2html 风格统一 diff 块 -->
                                        <div v-if="row._diff && row._diff.type === 'modified'" class="d2h-diff">
                                            <div class="d2h-line d2h-del">
                                                <span class="d2h-prefix">-</span>
                                                <span class="d2h-code">{{ row._diff.prevExpr }}</span>
                                            </div>
                                            <div class="d2h-line d2h-ins">
                                                <span class="d2h-prefix">+</span>
                                                <span class="d2h-code" v-html="row._diff.html"></span>
                                            </div>
                                            <div v-if="row._diff.changedFields && row._diff.changedFields.length"
                                                 class="d2h-fields">
                                                变更字段：
                                                <span v-for="f in row._diff.changedFields" :key="f"
                                                      class="d2h-field">{{ f }}</span>
                                            </div>
                                        </div>
                                        <!-- 新增: 整条为新增 -->
                                        <div v-else-if="row._diff && row._diff.type === 'added'" class="c-expr-text">
                                            <ins>{{ row.expr }}</ins>
                                        </div>
                                        <!-- 删除: 整条为删除 -->
                                        <div v-else-if="row._diff && row._diff.type === 'removed'" class="c-expr-text">
                                            <del>{{ row.expr }}</del>
                                        </div>
                                        <!-- 无变化 -->
                                        <div v-else class="c-expr-text">{{ row.expr }}</div>
                                    </template>
                                </template>
                            </el-table-column>

                            <!-- 原始文本: 始终只读, 可点击定位原文 -->
                            <el-table-column label="原始文本" min-width="280">
                                <template #default="{ row }">
                                    <el-input v-if="editMode" v-model="row.src_text" type="textarea" :rows="2" placeholder="来源依据或人工修改说明" /><div v-else-if="row.src_text" class="c-src-text c-src-link"
                                         title="点击在算子文档中定位原文"
                                         @click="showSrcInDoc(row)">
                                        {{ row.src_text }}
                                    </div>
                                    <span v-else class="c-src-text" style="color:var(--text-muted)">-</span>
                                </template>
                            </el-table-column>

                            <!-- 关联参数: 始终只读 -->
                            <el-table-column label="关联参数" min-width="200">
                                <template #default="{ row }">
                                    <el-select v-if="editMode" v-model="row.relation_params" multiple filterable allow-create default-first-option placeholder="输入关联参数后回车" /><div v-else class="c-params">
                                        <span v-for="p in (row.relation_params || [])" :key="p"
                                              class="c-param-chip">{{ p }}</span>
                                    </div>
                                </template>
                            </el-table-column>

                            <!-- 状态: 始终只读, 支持表头筛选 -->
                            <el-table-column label="状态" width="80" align="center"
                                             :filters="statusFilterOptions"
                                             :filter-method="(val, row) => !val ? true : (val === 'pass' ? !row._pflErrors.length : row._pflErrors.length)">
                                <template #default="{ row }">
                                    <el-tag :type="row._pflErrors.length ? 'danger' : 'info'" size="small">
                                        {{ row._pflErrors.length ? '关联失败' : '未关联失败' }}
                                    </el-tag>
                                </template>
                            </el-table-column>

                            <!-- 错误信息: 始终只读, 来自 analysis.json PFL -->
                            <el-table-column label="错误信息" min-width="220">
                                <template #default="{ row }">
                                    <div v-if="row._pflErrors.length"
                                         style="display:flex;flex-direction:column;gap:3px">
                                        <div v-for="(e, ei) in row._pflErrors" :key="ei"
                                             style="font-size:12px;color:var(--red);white-space:pre-wrap;word-break:break-word">
                                            <span style="font-weight:600">[{{ e.id }}]</span> {{ e.target }}
                                        </div>
                                    </div>
                                    <span v-else style="color:var(--text-muted)">-</span>
                                </template>
                            </el-table-column>

                            <!-- 操作列: 仅编辑态 -->
                            <el-table-column v-if="editMode" label="" width="50" align="center">
                                <template #default="{ $index }">
                                    <el-button size="small" type="danger" plain circle
                                               @click="removeConstraint($index)">✕
                                    </el-button>
                                </template>
                            </el-table-column>
                        </el-table>
                    </div>
                </div>
                </div>

                <!-- 输入参数 -->
                <div class="card" :class="{expanded: activePanels.includes('inputs')}" data-panel="io">
                    <div class="card-header" @click="togglePanel('inputs')">
                        <div class="title-row">
                            <div class="card-title">
                                <div class="icon icon-input">📥</div>
                                输入参数 Inputs
                            </div>
                            <div class="card-count">{{ inputRows.length }} 个参数</div>
                        </div>
                        <div class="card-toggle" :class="{open: activePanels.includes('inputs')}">▾</div>
                    </div>
                    <div class="card-body" v-show="activePanels.includes('inputs')">
                        <div class="table-pad">
                        <el-table :data="inputRows" class="op-table" :max-height="tableMaxHeight">
                            <el-table-column label="参数名" min-width="120" show-overflow-tooltip>
                                <template #default="{ row }"><span class="param-name mono">{{ row.name }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="类型 Type" min-width="110" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.type) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="格式 Format" min-width="110" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.format) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="数据类型 DType" min-width="160" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.dtype) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="维度 Dimensions" min-width="100" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.dimensions) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="数组长度" min-width="90" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.array_length)
                                    }}</span></template>
                            </el-table-column>
                            <el-table-column label="可选" width="70" align="center">
                                <template #default="{ row }">{{ boolText(row.p.is_optional) }}</template>
                            </el-table-column>
                            <el-table-column label="非连续" width="80" align="center">
                                <template #default="{ row }">{{ boolText(row.p.is_support_discontinuous) }}</template>
                            </el-table-column>
                            <el-table-column label="描述" min-width="220" show-overflow-tooltip>
                                <template #default="{ row }"><span>{{ cellText(row.p.description) || '-' }}</span>
                                </template>
                            </el-table-column>
                        </el-table>
                    </div>
                </div>
                </div>

                <!-- 输出参数 -->
                <div class="card" :class="{expanded: activePanels.includes('outputs')}" data-panel="io">
                    <div class="card-header" @click="togglePanel('outputs')">
                        <div class="title-row">
                            <div class="card-title">
                                <div class="icon icon-output">📤</div>
                                输出参数 Outputs
                            </div>
                            <div class="card-count">{{ outputRows.length }} 个参数</div>
                        </div>
                        <div class="card-toggle" :class="{open: activePanels.includes('outputs')}">▾</div>
                    </div>
                    <div class="card-body" v-show="activePanels.includes('outputs')">
                        <div class="table-pad">
                        <el-table :data="outputRows" class="op-table" :max-height="tableMaxHeight">
                            <el-table-column label="参数名" min-width="120" show-overflow-tooltip>
                                <template #default="{ row }"><span class="param-name mono">{{ row.name }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="类型 Type" min-width="110" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.type) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="格式 Format" min-width="110" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.format) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="数据类型 DType" min-width="160" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.dtype) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="维度 Dimensions" min-width="100" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.dimensions) }}</span>
                                </template>
                            </el-table-column>
                            <el-table-column label="数组长度" min-width="90" show-overflow-tooltip>
                                <template #default="{ row }"><span class="mono">{{ cellText(row.p.array_length)
                                    }}</span></template>
                            </el-table-column>
                            <el-table-column label="可选" width="70" align="center">
                                <template #default="{ row }">{{ boolText(row.p.is_optional) }}</template>
                            </el-table-column>
                            <el-table-column label="非连续" width="80" align="center">
                                <template #default="{ row }">{{ boolText(row.p.is_support_discontinuous) }}</template>
                            </el-table-column>
                            <el-table-column label="描述" min-width="220" show-overflow-tooltip>
                                <template #default="{ row }"><span>{{ cellText(row.p.description) || '-' }}</span>
                                </template>
                            </el-table-column>
                        </el-table>
                    </div>
                </div>
                </div>
        </div>

    </div>

    <!-- 原文定位弹框 -->
    <el-dialog v-model="docDialogVisible" :title="docDialogTitle" width="70%" top="5vh" destroy-on-close>
        <div v-if="docDialogLoading" style="text-align:center;padding:40px;color:var(--text-muted)">
            加载算子文档中...
        </div>
        <div v-else-if="docDialogError" style="text-align:center;padding:40px;color:var(--red)">
            {{ docDialogError }}
        </div>
        <template v-else>
            <div class="doc-mode-bar">
                <button class="doc-mode-btn" :class="{on: docMode === 'rendered'}" @click="docMode = 'rendered'">渲染视图</button>
                <button class="doc-mode-btn" :class="{on: docMode === 'raw'}" @click="docMode = 'raw'">原文（带行号）</button>
                <span v-if="docHitLines.size" class="doc-mode-hint">
                    黄底为约束来源行（src_txt_line：{{ [...docHitLines].map(i => i + 1).join('、') }}），已自动滚动到首个命中处
                </span>
                <span v-else class="doc-mode-hint">该约束未记录来源行号，无法定位</span>
            </div>
            <!-- markdown 渲染视图 -->
            <div v-if="docMode === 'rendered'" class="doc-md" ref="docMdRef">
                <div v-if="docHtml" v-html="docHtml"></div>
                <div v-else class="doc-md-empty">（文档为空或渲染失败，请切到「原文」查看）</div>
            </div>
            <!-- 原文行号视图（可高亮定位） -->
            <div v-else class="doc-viewer" ref="docViewerRef">
                <div v-for="(line, li) in docLines" :key="li"
                     class="doc-line"
                     :class="{'doc-line-hit': docHitLines.has(li)}">
                    <span class="doc-line-no">{{ li + 1 }}</span>
                    <span class="doc-line-text">{{ line }}</span>
                </div>
            </div>
        </template>
        <template #footer>
            <span style="margin-right:auto;font-size:12px;color:var(--text-muted)">
                共 {{ docLines.length }} 行，高亮 {{ docHitLines.size }} 行匹配
            </span>
            <el-button @click="docDialogVisible = false">关闭</el-button>
        </template>
    </el-dialog>
    
</div>
</template>
<style scoped>
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

.legacy-review {
    --bg: #f4f6f9;
    --panel-bg: #ffffff;
    --border: #e2e8f0;
    --text: #1e293b;
    --text-secondary: #64748b;
    --text-muted: #94a3b8;
    --accent: #3b82f6;
    --green: #10b981;
    --red: #ef4444;
    --orange: #f59e0b;
    --code-bg: #f8fafc;
    --code: #475569;
}

.legacy-review {
    height: 100%;
}

.legacy-review {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}

.legacy-review {
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* ===== 顶栏 ===== */
.topbar {
    background: var(--panel-bg);
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
    flex-wrap: wrap;
    min-height: 56px;
}

.topbar-title {
    font-size: 15px;
    font-weight: 600;
}

.topbar-right {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
}

/* ===== 副信息栏 ===== */
.info-bar {
    background: var(--panel-bg);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    min-height: 80px;
    flex-shrink: 0;
    flex-wrap: wrap;
}

.info-left {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    min-width: 0;
}

.info-right {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    min-width: 0;
    margin-left: auto;
}

.divider-v {
    width: 1px;
    height: 20px;
    background: var(--border);
    flex-shrink: 0;
}

.stat-item {
    display: flex;
    align-items: baseline;
    gap: 4px;
    font-size: 12px;
    color: var(--text-secondary);
    white-space: nowrap;
}

.stat-num {
    font-weight: 700;
    font-size: 14px;
    line-height: 1.2;
}

.stat-pass .stat-num {
    color: var(--green);
}

.stat-fail .stat-num {
    color: var(--red);
}

.stat-warn .stat-num {
    color: var(--orange);
}

.stat-running .stat-num {
    color: var(--accent);
}

.stat-total .stat-num {
    color: var(--text);
}

.stat-succ .stat-num {
    color: var(--green);
}

.stat-bad .stat-num {
    color: var(--red);
}

.field-label {
    font-size: 13px;
    color: var(--text-secondary);
    font-weight: 500;
    white-space: nowrap;
}

/* 响应式: 小屏适配 (时间线间距固定 56px, 超出时水平滚动) */
@media (max-width: 1600px) {
    .info-left .el-select {
        width: 240px !important;
    }

    .info-right .el-select {
        width: 240px !important;
    }
}

@media (max-width: 1400px) {
    .info-left .el-select {
        width: 200px !important;
    }

    .info-right .el-select {
        width: 200px !important;
    }
}

@media (max-width: 1100px) {
    .info-bar {
        gap: 10px;
    }

    .info-left .el-select {
        width: 160px !important;
    }

    .info-right .el-select {
        width: 160px !important;
    }

    .timeline-node {
        min-width: 44px;
    }

    .node-label {
        font-size: 11px;
    }
}

@media (max-width: 900px) {
    .info-left .el-select {
        width: 130px !important;
    }

    .info-right .el-select {
        width: 130px !important;
    }

    .node-time {
        display: none;
    }

    .stat-item {
        font-size: 11px;
    }
}

@media (max-width: 700px) {
    .info-left .el-select {
        width: 110px !important;
    }

    .info-right .el-select {
        width: 110px !important;
    }

    .field-label {
        font-size: 12px;
    }

    .field-label {
        font-size: 12px;
    }
}

/* ===== 主体: 页面滚动, 表格显示完整 ===== */
.main {
    flex: 1;
    min-height: 0;
    margin: 16px;
    margin-bottom: 28px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    overflow-y: auto;
}

/* div 卡片: 替代 el-collapse, 自然高度 */
.cards {
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.card {
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    display: flex;
    flex-direction: column;
}

.card-header {
    padding: 8px 16px;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: var(--panel-bg);
    border-bottom: 1px solid var(--border);
    user-select: none;
    cursor: pointer;
    transition: background 0.12s;
    position: sticky;
    top: 0;
    z-index: 10;
}

.card.expanded .card-header {
    border-radius: 0;
}

.card-header:hover {
    background: #fafbfc;
}

.card-toggle {
    margin-left: auto;
    color: var(--text-muted);
    font-size: 14px;
    line-height: 1;
    transition: transform 0.2s;
}

.card-toggle.open {
    transform: rotate(180deg);
}

.card-body {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

/* 约束卡片: 自然高度, 滚动交给 .main */
.cards [data-panel="rel"] .card-body {
    overflow: visible;
}

.cards [data-panel="rel"] .constraint-area {
    padding-bottom: 0;
}

.title-row {
    display: flex;
    align-items: center;
    justify-content: left;
    gap: 12px;
    width: 100%;
}

.card-title {
    font-size: 14px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
}

.card-title .icon {
    width: 28px;
    height: 28px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
}

.icon-constraint {
    background: #ede9fe;
}

.icon-input {
    background: #dbeafe;
}

.icon-output {
    background: #ccfbf1;
}

.card-count {
    font-size: 12px;
    color: var(--text-muted);
}

/* 表格: 显示完整内容, 无内部滚动 */
.table-pad {
    padding: 10px;
}

.op-table {
    width: 100%;
}

.op-table .el-table__header th {
    background: #fafbfc !important;
    color: var(--text-secondary);
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}

/* 固定表头: 通过 el-table max-height 启用原生固定表头（.el-table overflow:hidden 会使 CSS sticky 失效） */
html[data-theme='dark'] .op-table .el-table__header th {
    background: #182136 !important;
}

.op-table .cell {
    font-size: 13px;
}

/* 等宽字体字段 */
.mono {
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
}

.param-name {
    font-weight: 600;
    color: var(--text);
}

/* 表达式代码块 */
.expr-block {
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px;
    color: var(--code);
    background: var(--code-bg);
    padding: 8px 12px;
    border-radius: 6px;
    word-break: break-all;
    line-height: 1.5;
    border: 1px solid #f1f5f9;
}

.src-block {
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px;
    color: var(--text);
    word-break: break-all;
    line-height: 1.5;
}

/* 运行态转圈 */
.spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid currentColor;
    border-right-color: transparent;
    border-radius: 50%;
    vertical-align: -1.5px;
    margin-right: 5px;
    animation: spin 0.9s linear infinite;
}

@keyframes spin {
    to {
        transform: rotate(360deg);
    }
}

/* 约束面板: 表格 + 行内编辑 */
.constraint-area {
    padding: 4px 12px 12px;
}

.constraint-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 4px 0 10px;
    position: sticky;
    top: 0;
    z-index: 10;
    padding: 8px 20px;
    border-bottom: 1px solid var(--border);
}

.constraint-toolbar .toolbar-hint {
    font-size: 12px;
    color: var(--text-muted);
}

/* 表格内文本 */
.c-expr-text {
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px;
    color: var(--code);
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
}

.c-src-text {
    font-size: 12px;
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
}

.c-src-link {
    cursor: pointer;
    color: var(--accent);
    text-decoration: underline dotted;
    text-underline-offset: 2px;
}

.c-src-link:hover {
    color: #2563eb;
    text-decoration-style: solid;
}

/* 原文定位弹框 */
.doc-viewer {
    max-height: 65vh;
    overflow-y: auto;
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 12px;
    line-height: 1.6;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: #fafbfc;
}

/* ---- 原文弹框: 模式切换 + markdown 渲染视图 ---- */
.doc-mode-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
}
.doc-mode-btn {
    border: 1px solid var(--border);
    background: var(--panel-bg);
    color: var(--text-secondary);
    font-size: 12px;
    padding: 4px 14px;
    border-radius: 6px;
    cursor: pointer;
}
.doc-mode-btn.on {
    color: var(--accent);
    border-color: var(--accent);
    background: #eff6ff;
    font-weight: 600;
}
.doc-mode-hint {
    font-size: 11px;
    color: var(--text-muted);
}
.doc-md {
    max-height: 65vh;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel-bg);
    padding: 18px 24px;
    font-size: 13px;
    line-height: 1.7;
}
.doc-md h1, .doc-md h2, .doc-md h3, .doc-md h4 {
    margin: 18px 0 8px;
    color: var(--text);
    line-height: 1.4;
}
.doc-md h1 { font-size: 20px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
.doc-md h2 { font-size: 17px; }
.doc-md h3 { font-size: 15px; }
.doc-md h4 { font-size: 13.5px; }
.doc-md p { margin: 8px 0; }
.doc-md ul, .doc-md ol { margin: 8px 0; padding-left: 22px; }
.doc-md li { margin: 3px 0; }
.doc-md code {
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 12px;
    background: var(--code-bg);
    color: var(--code);
    padding: 1px 5px;
    border-radius: 4px;
}
.doc-md pre {
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    overflow-x: auto;
    margin: 10px 0;
}
.doc-md pre code { background: none; padding: 0; }
.doc-md blockquote {
    border-left: 3px solid var(--accent);
    background: #f8faff;
    margin: 10px 0;
    padding: 8px 14px;
    color: var(--text-secondary);
}
.doc-md table {
    border-collapse: collapse;
    margin: 12px 0;
    width: 100%;
    font-size: 12.5px;
}
.doc-md th, .doc-md td {
    border: 1px solid var(--border);
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
}
.doc-md th {
    background: #f1f5f9;
    font-weight: 600;
    white-space: nowrap;
}
.doc-md tr:nth-child(even) td { background: #fafbfc; }
html[data-theme='dark'] .doc-md tr:nth-child(even) td { background: #141a2b; }
html[data-theme='dark'] .doc-md th { background: #182136; }
html[data-theme='dark'] .doc-mode-btn.on { background: #1a2440; }
html[data-theme='dark'] .doc-md blockquote { background: #16203a; }
.doc-md-empty {
    text-align: center;
    padding: 40px 0;
    color: var(--text-muted);
    font-size: 13px;
}
/* 渲染视图中的命中行锚点：零宽 span 染色所在文本块 */
.doc-md .doc-md-hit {
    display: inline;
    background: #fff3bf;
    box-shadow: -6px 0 0 0 #fff3bf, 6px 0 0 0 #fff3bf;
    border-radius: 2px;
    scroll-margin-top: 80px;
}
html[data-theme='dark'] .doc-md .doc-md-hit {
    background: #5c4a1e;
    box-shadow: -6px 0 0 0 #5c4a1e, 6px 0 0 0 #5c4a1e;
}


.doc-line {
    display: flex;
    gap: 12px;
    padding: 1px 8px;
    border-bottom: 1px solid #f0f0f0;
}

.doc-line-no {
    min-width: 36px;
    text-align: right;
    color: var(--text-muted);
    user-select: none;
    flex-shrink: 0;
}

.doc-line-text {
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--text);
}

.doc-line-hit {
    background: #fff8e1;
    border-left: 3px solid var(--orange);
}

.doc-line-hit .doc-line-no {
    color: var(--orange);
    font-weight: 600;
}

.doc-line-text mark {
    background: #ffe082;
    color: inherit;
    padding: 0 1px;
    border-radius: 2px;
}

.c-type-tag {
    font-size: 11px;
    padding: 1px 8px;
    border-radius: 10px;
    background: #ede9fe;
    color: #7c3aed;
    font-weight: 500;
    white-space: nowrap;
}

.c-param-chip {
    font-size: 11px;
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--code-bg);
    color: var(--text-secondary);
    border: 1px solid #f1f5f9;
}

.c-params {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
}

/* 编辑态单元格 */
.c-cell-edit .el-textarea__inner {
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px;
}

.c-tag-wrap {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
}

.c-tag-add {
    width: 120px;
    font-size: 12px;
}

/* el-tag 状态色 */
.state-tag {
    font-weight: 600;
}

/* ===== 轮次对比: 表格上方数量差异统计 ===== */
.diff-stats {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 10px;
}

.diff-stats-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    margin-right: 4px;
}

.diff-stat-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    padding: 3px 10px;
    border-radius: 14px;
    background: var(--panel-bg);
    border: 1px solid var(--border);
    color: var(--text-secondary);
}

.diff-stat-chip b {
    font-weight: 700;
}

.diff-stat-chip .arrow {
    color: var(--text-muted);
    margin: 0 2px;
}

.diff-stat-chip.clickable {
    cursor: pointer;
    transition: all .15s;
}

.diff-stat-chip.clickable:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 6px rgba(0, 0, 0, .12);
}

.diff-stat-chip.clickable.active {
    outline: 2px solid currentColor;
    outline-offset: -1px;
}

.diff-stat-chip.active.added {
    background: #dcfce7;
}

.diff-stat-chip.active.removed {
    background: #fee2e2;
}

.diff-stat-chip.active.modified {
    background: #fef3c7;
}

.diff-reset-btn {
    margin-left: 4px;
    font-size: 12px;
    padding: 3px 12px;
    border-radius: 14px;
    border: 1px solid var(--border);
    background: var(--panel-bg);
    color: var(--text-secondary);
    cursor: pointer;
    transition: all .15s;
}

.diff-reset-btn:hover {
    border-color: var(--accent, #409eff);
    color: var(--accent, #409eff);
}

.diff-stat-chip.added {
    background: #f0fdf4;
    border-color: #bbf7d0;
    color: #15803d;
}

.diff-stat-chip.removed {
    background: #fef2f2;
    border-color: #fecaca;
    color: #b91c1c;
}

.diff-stat-chip.modified {
    background: #fffbeb;
    border-color: #fde68a;
    color: #b45309;
}

.diff-stat-chip.none {
    color: var(--text-muted);
}

/* ===== diff2html 风格: 约束轮次对比 ===== */
.d2h-diff {
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
    font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 12px;
    line-height: 1.7;
    background: #fafbfc;
}

.d2h-line {
    display: flex;
    align-items: baseline;
    padding: 1px 0;
    white-space: pre-wrap;
    word-break: break-word;
}

.d2h-line + .d2h-line {
    border-top: 1px solid #eef2f7;
}

/* 删除行: 红底 + 左侧红竖条 */
.d2h-line.d2h-del {
    background: #ffecec;
    border-left: 3px solid #e53935;
    padding-left: 5px;
}

/* 新增行: 绿底 + 左侧绿竖条 */
.d2h-line.d2h-ins {
    background: #eaffee;
    border-left: 3px solid #43a047;
    padding-left: 5px;
}

/* +/- 前缀符号 */
.d2h-prefix {
    flex-shrink: 0;
    width: 14px;
    text-align: center;
    font-weight: 700;
    user-select: none;
}

.d2h-del .d2h-prefix {
    color: #c62828;
}

.d2h-ins .d2h-prefix {
    color: #2e7d32;
}

/* 代码内容: 删除行带删除线 */
.d2h-code {
    flex: 1;
    min-width: 0;
    color: var(--code);
}

.d2h-del .d2h-code {
    color: #991b1b;
    text-decoration: line-through;
    text-decoration-color: rgba(153, 27, 27, .45);
}

/* 变更字段标签 */
.d2h-fields {
    padding: 4px 8px;
    border-top: 1px dashed var(--border);
    font-size: 11px;
    color: var(--text-muted);
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    font-family: -apple-system, sans-serif;
}

.d2h-field {
    background: var(--panel-bg);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 0 5px;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 10px;
    color: var(--text-secondary);
}

/* 约束表内 diff 标记 (新增/删除整条) */
.op-table ins {
    background: #d1fae5;
    color: #065f46;
    text-decoration: none;
    border-radius: 2px;
    padding: 0 1px;
}

.op-table del {
    background: #fee2e2;
    color: #991b1b;
    border-radius: 2px;
    padding: 0 1px;
}

/* 时间线: 单独一行, 位于 constraint-toolbar 上方 */
.timeline-bar {
    margin: 0 16px;
    padding: 4px 0;
    margin-top: 12px;
}

/* 时间线标题行 + 折叠开关 */
.timeline-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
}
.timeline-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
}
.timeline-title em {
    font-style: normal;
    font-weight: 400;
    font-size: 11px;
    color: var(--text-muted);
    margin-left: 6px;
}
.timeline-toggle {
    border: 1px solid var(--border);
    background: var(--panel-bg);
    color: var(--accent);
    font-size: 11px;
    padding: 2px 12px;
    border-radius: 6px;
    cursor: pointer;
    white-space: nowrap;
}
.timeline-toggle:hover { background: #eff6ff; }
html[data-theme='dark'] .timeline-toggle:hover { background: #1a2440; }

/* 横向执行历史 timeline: 水平滚动 */
.history-wrap {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
}

.timeline-scroll {
    flex: 1;
    min-width: 0;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: thin;
    scrollbar-color: #c1c8d4 #f1f5f9;
}

.timeline-scroll::-webkit-scrollbar {
    height: 6px;
}

.timeline-scroll::-webkit-scrollbar-track {
    background: #f1f5f9;
    border-radius: 3px;
}

.timeline-scroll::-webkit-scrollbar-thumb {
    background: #c1c8d4;
    border-radius: 3px;
}

.timeline-scroll::-webkit-scrollbar-thumb:hover {
    background: #94a3b8;
}

.op-timeline-h {
    display: flex;
    align-items: flex-start;
    justify-content: center;
    gap: 56px;
    padding: 4px 16px;
    white-space: nowrap;
    min-width: 100%;
}

.timeline-node {
    display: flex;
    flex-direction: column;
    align-items: center;
    position: relative;
    min-width: 60px;
}

.node-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--text-muted);
    border: 2px solid #fff;
    box-shadow: 0 0 0 1px var(--border);
    z-index: 1;
}

.timeline-node.success .node-dot {
    background: var(--green);
}

.timeline-node.danger .node-dot {
    background: var(--red);
}

.timeline-node.primary .node-dot {
    background: var(--accent);
}

.timeline-node.info .node-dot {
    background: var(--text-muted);
}

.timeline-node.running .node-dot {
    background: var(--accent);
    animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% {
        box-shadow: 0 0 0 1px var(--accent);
    }
    50% {
        box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.3);
    }
}

.node-label {
    font-size: 12px;
    font-weight: 500;
    color: var(--text);
    margin-top: 4px;
    white-space: nowrap;
}

.timeline-node.running .node-label {
    color: var(--accent);
    font-weight: 600;
}

.node-time {
    font-size: 10px;
    color: var(--text-muted);
    margin-top: 2px;
    white-space: nowrap;
}

.node-line {
    position: absolute;
    top: 5px;
    left: calc(50% + 5px);
    width: calc(100% + 46px);
    height: 2px;
    background: var(--border);
    z-index: 0;
}

.timeline-node:last-child .node-line {
    display: none;
}

.history-empty {
    font-size: 12px;
    color: var(--text-muted);
}


.save-note{margin:8px 24px;color:var(--text-secondary);font-size:12px}a{color:#2774ed;text-decoration:none}

</style>
