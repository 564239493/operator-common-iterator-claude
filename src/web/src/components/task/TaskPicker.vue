<script setup lang="ts">
import { computed } from 'vue'
import { useTask } from '../../composables/useTask'
import { fmtTaskOption, fmtTaskTime, stateTagType } from './group'
import type { RunSummary } from '../../api/types'

/**
 * 全站统一的任务/覆盖率选择器，三种模式：
 * - default（工作台等）：按算子分组，组内为该算子的一次次测试运行（按创建时间倒序）。
 *   组件内部持有共享 store 的读写，页面不跨 API 范式做 v-model。
 * - runs（约束审核 / 覆盖率-任务内嵌报告）：runs 下目录平铺，每一个目录名为一条数据。
 *   未传 modelValue 时回退共享 store（selectedRunId/selectRun）；传了则由页面受控。
 * - covtree（覆盖率-全局最终报告）：按 ops_final_cov_report 目录层级展示（树结构下拉），
 *   可选中任意层级节点，值为 '/' 连接的全路径（与页面 path 一致）。
 */
type Mode = 'default' | 'runs' | 'covtree'
 
const props = withDefaults(defineProps<{
  disabled?: boolean
  mode?: Mode
  /** runs 模式：自定义选项列表（默认全站任务列表 useTask().runs） */
  list?: RunSummary[]
  /** covtree 模式：ops_final_cov_report 覆盖率树（{versions:[...]}） */
  tree?: { versions?: any[] } | null
  /** runs: run_id；covtree: '/' 连接的全路径；未传时 runs 模式回退共享 store */
  modelValue?: string
}>(), {
  mode: 'default',
  list: () => [],
  tree: null,
  modelValue: undefined,
})
 
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()
 
const { runs, selectedRunId, selectedRun, groups, selectRun } = useTask()
 
/* ===== default 模式：按算子分组（原行为） ===== */
const value = computed({
  get: () => selectedRunId.value,
  set: (id: string) => selectRun(id),
})
const current = computed(() => selectedRun.value)
const frameworkText = (r: { test_framework?: string; mode?: string }) =>
  `${r.test_framework || '—'} · ${r.mode || '—'}`

/* ===== runs 模式：runs 下目录平铺，每个目录名为一条数据 ===== */
const runsOptions = computed(() => (props.list && props.list.length ? props.list : runs.value))
const runsValue = computed({
  get: () => (props.modelValue !== undefined ? props.modelValue : selectedRunId.value),
  set: (v: string) => {
    if (props.modelValue !== undefined) emit('update:modelValue', v)
    else selectRun(v)
  },
})
 
/* ===== covtree 模式：ops_final_cov_report 目录层级（树结构下拉） ===== */
const versions = computed(() => (props.tree && Array.isArray(props.tree.versions) ? props.tree.versions : []))
/** 转 el-tree-select 数据：每节点带 value=层级路径（与页面 path.join('/') 一致，node-key 取 value），
 *  并透传原节点的 summary/kind/has_analysis/version 等字段供下拉渲染使用 */
function toTreeData(v: any) {
  const map = (n: any, prefix: string[]): any => {
    const path = [...prefix, n.name]
    const node: any = { ...n, name: n.name, value: path.join('/') }
    if (Array.isArray(n.children) && n.children.length) node.children = n.children.map((c: any) => map(c, path))
    return node
  }
  return map(v, [])
}
const treeData = computed(() => versions.value.map(toTreeData))
const covValue = computed({
  get: () => props.modelValue ?? '',
  set: (v: string) => emit('update:modelValue', v),
})
 
/* ===== 树节点覆盖率指标（每层展示 函数/行/分支 覆盖率） ===== */
const fmtRate = (r: number | undefined | null) => (typeof r !== 'number' ? '—' : `${Math.round(r)}%`)
const rateCls = (r: number | undefined | null) => {
  if (typeof r !== 'number' || r < 50) return 'low'
  return r >= 80 ? 'ok' : 'mid'
}
</script>

<template>
 <!-- default：按算子分组（工作台等页面原有行为） -->
  <el-select
    v-if="mode === 'default'"
    v-model="value"
    class="task-picker"
    filterable
    placeholder="选择任务"
    :disabled="props.disabled"
    data-test="task-picker"
  >
    <template #label>
      <span v-if="current" class="picked">
        <b>{{ current.operator || current.run_id }}</b>
        <span class="picked-time">{{ fmtTaskTime(current.created_at) }}</span>
        <span class="picked-state">{{ current.state || '…' }}</span>
      </span>
    </template>
    <el-option-group
      v-for="g in groups"
      :key="g.operator"
      :label="`${g.operator}（${g.runs.length}）`"
    >
      <el-option
        v-for="r in g.runs"
        :key="r.run_id"
        :value="r.run_id"
        :label="fmtTaskOption(r)"
        :title="r.run_id"
      >
        <div class="task-option">
          <span class="task-time">{{ fmtTaskTime(r.created_at) }}</span>
          <el-tag v-if="r.state" size="small" :type="stateTagType(r.state)" effect="light">{{ r.state }}</el-tag>
          <span class="task-meta">第 {{ r.current_iteration ?? '—' }}/{{ r.max_iterations ?? '—' }} 轮</span>
          <span class="task-meta meta-dim">{{ frameworkText(r) }}</span>
        </div>
      </el-option>
    </el-option-group>
    <el-option v-if="!groups.length && !runs.length" value="" label="暂无可解析的任务" disabled />
  </el-select>

  <!-- runs：runs 下目录平铺，每个目录名为一条数据 -->
  <el-select
    v-else-if="mode === 'runs'"
    v-model="runsValue"
    class="task-picker"
    filterable
    placeholder="选择任务目录"
    :disabled="props.disabled"
  >
    <el-option v-for="r in runsOptions" :key="r.run_id" :value="r.run_id" :label="r.run_id" :title="r.run_id" />
    <el-option v-if="!runsOptions.length" value="" label="暂无任务目录" disabled />
  </el-select>
 
  <!-- covtree：ops_final_cov_report 目录层级（树结构下拉，可选中任意层级） -->
  <el-tree-select
    v-else
    v-model="covValue"
    class="task-picker cov-tree"
    popper-class="cov-tree-popper"
    placement="bottom-end"
    :data="treeData"
    :props="{ label: 'name', children: 'children' }"
    node-key="value"
    check-strictly
    :expand-on-click-node="false"
    :render-after-expand="false"
    default-expand-all
    placeholder="选择覆盖率目录"
    :disabled="props.disabled"
  >
    <template #label="{ label, value }">
      <span v-if="value" class="cov-picked" :title="value">
        <b class="cov-picked-name">{{ label }}</b>
        <span class="cov-picked-path">{{ value }}</span>
      </span>
    </template>
    <!-- 树节点：单行内容（层级标签前置 / 层级名 / 函数·行·分支 覆盖率数值；第 5 层算子展示「算子」标签，第 6 层产品目录展示「产品」标签） -->
    <template #default="{ node, data }">
      <span class="cov-node">
        <span v-if="node && node.level === 5" class="cov-op">算子</span>
        <span v-if="node && node.level === 6" class="cov-prod">产品</span>
        <span class="cov-node-name" :title="data.name">{{ data.name }}</span>
        <span v-if="data && data.summary" class="cov-node-metrics">
          <span class="m" :class="rateCls(data.summary.func)" :title="`函数覆盖率 ${fmtRate(data.summary.func)}`">
            <em class="mlab">函数</em><b>{{ fmtRate(data.summary.func) }}</b>
          </span>
          <span class="m" :class="rateCls(data.summary.line)" :title="`行覆盖率 ${fmtRate(data.summary.line)}`">
            <em class="mlab">行</em><b>{{ fmtRate(data.summary.line) }}</b>
          </span>
          <span class="m" :class="rateCls(data.summary.branch)" :title="`分支覆盖率 ${fmtRate(data.summary.branch)}`">
            <em class="mlab">分支</em><b>{{ fmtRate(data.summary.branch) }}</b>
          </span>
        </span>
        <span v-else class="cov-node-metrics">
          <span class="m none"><em class="mlab">覆盖率</em><b>—</b></span>
        </span>
      </span>
    </template>
  </el-tree-select>
</template>

<style scoped>
.task-picker { width: 320px; }
.picked { display: inline-flex; align-items: baseline; gap: 8px; min-width: 0; }
.picked b { color: var(--wb-ink); font-weight: 600; }
.picked-time { color: var(--wb-muted); font-size: 12px; }
.picked-state { color: var(--wb-faint); font-size: 12px; }
.task-option { display: flex; align-items: center; gap: 8px; min-width: 0; }
.task-time { color: var(--wb-ink); font-variant-numeric: tabular-nums; }
.task-meta { color: var(--wb-muted); font-size: 12px; white-space: nowrap; }
.meta-dim { color: var(--wb-faint); margin-left: auto; }

/* covtree 选中展示：算子名（粗）+ 完整层级路径（弱化、超长省略） */
.cov-picked { display: inline-flex; align-items: baseline; gap: 6px; min-width: 0; max-width: 100%; overflow: hidden; }
.cov-picked-name { color: var(--wb-ink); font-weight: 600; white-space: nowrap; flex-shrink: 0; }
.cov-picked-path { color: var(--wb-muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>

<!-- 树结构下拉弹层 teleport 到 body，需全局样式 -->
<style>
.cov-tree-popper {
  width: 540px !important;
  max-width: calc(100vw - 24px) !important;
  font-size: 13px;
}
.cov-tree-popper .el-select-dropdown__wrap {
  max-height: 430px;
}
.cov-tree-popper .el-select-dropdown__list {
  padding: 5px;
}
 
.cov-tree-popper .el-tree {
  padding: 0;
  background: transparent;
  color: var(--wb-ink);
}

/* 行：单行内容、圆角悬浮，内容垂直居中，选中（is-current）蓝底强调 */
.cov-tree-popper .el-tree-node__content {
  height: auto;
  min-height: 30px;
  display: flex;
  align-items: center;
  border-radius: 8px;
  margin: 2px 0;
  padding-right: 8px;
  transition: background .15s ease;
}
.cov-tree-popper .el-tree-node__content:hover {
  background: var(--wb-hover);
}
.cov-tree-popper .el-tree-node.is-current > .el-tree-node__content {
  background: var(--wb-blue-soft);
}
.cov-tree-popper .el-tree-node.is-current > .el-tree-node__content .cov-node-name {
  color: var(--wb-blue);
}

/* ===== 树连线（竖线 + 横线）：画在行内容自身（::before 竖线段 / ::after 横线支线），
 * 伪元素是行的子元素 → 悬浮/选中背景在下、连线在上，不会被遮挡。
 * 竖线对齐父节点展开图标列（缩进 18px/层，图标中心 = (d-1)*18 + 3.5），行上下各外延 2px
 * 使相邻行竖线连成整段、首行上延恰好触达父节点行底（顶端不悬空、不越过按钮）；
 * 末行竖线收在行底。横线从竖线连到该行内容起点（长度 = 18 - 3.5 = 14.5px）。 ===== */
.cov-tree-popper .el-tree-node__content { position: relative; }
 
.cov-tree-popper .el-tree-node__children > .el-tree-node > .el-tree-node__content::before {
  content: '';
  position: absolute;
  top: -2px;
  bottom: -2px;
  width: 1px;
  background: var(--wb-line-soft);
}
.cov-tree-popper .el-tree-node__children > .el-tree-node > .el-tree-node__content::after {
  content: '';
  position: absolute;
  top: 50%;
  height: 1px;
  background: var(--wb-line-soft);
}
/* 末行：竖线不收进下边距 */
.cov-tree-popper .el-tree-node__children > .el-tree-node:last-child > .el-tree-node__content::before {
  bottom: 0;
}

/* 第 2 层 算子库：竖线 x=3.5（版本图标列），横线 3.5→18 */
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::before { left: 3.5px; }
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::after { left: 3.5px; width: 14.5px; }
/* 第 3 层 域：竖线 x=21.5（算子库图标列），横线 21.5→36 */
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::before { left: 21.5px; }
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::after { left: 21.5px; width: 14.5px; }
/* 第 4 层 算子目录：竖线 x=39.5（域图标列），横线 39.5→54 */
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::before { left: 39.5px; }
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::after { left: 39.5px; width: 14.5px; }
/* 第 5 层 算子：竖线 x=57.5（算子目录图标列），横线 57.5→72 */
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::before { left: 57.5px; }
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::after { left: 57.5px; width: 14.5px; }
/* 第 6 层 产品：竖线 x=75.5（算子图标列），横线 75.5→90 */
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::before { left: 75.5px; }
.cov-tree-popper .el-tree > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__children > .el-tree-node > .el-tree-node__content::after { left: 75.5px; width: 14.5px; }

/* +/- 展开收起图标：替换默认箭头（点击展开/收起仍由 el-tree 原生处理），紧凑尺寸 */
.cov-tree-popper .el-tree-node__expand-icon {
  flex-shrink: 0;
  width: 5px;
  height: 5px;
  margin: 0 5px 0 1px;
  border-radius: 4px;
  background: var(--wb-line-soft);
  border: 1px solid var(--wb-line);
  color: var(--wb-ink);
  font-size: 11px;
  line-height: 13px;
  text-align: center;
  font-style: normal;
  transform: none !important;
  transition: background .15s ease, color .15s ease, border-color .15s ease;
}
.cov-tree-popper .el-tree-node__expand-icon:hover {
  background: var(--wb-blue-soft);
  border-color: var(--wb-blue-line);
  color: var(--wb-blue);
}
.cov-tree-popper .el-tree-node__expand-icon svg { display: none; }
.cov-tree-popper .el-tree-node__expand-icon::before { content: '+'; }
.cov-tree-popper .el-tree-node__expand-icon.expanded::before { content: '−'; }
/* 叶子：占位保持对齐，不显示 +/- */
.cov-tree-popper .el-tree-node__expand-icon.is-leaf {
  visibility: hidden;
}

/* 节点内容：单行（固定宽度层级名 + 算子标签 + 覆盖率指标），垂直居中 */
.cov-tree-popper .cov-node {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}
.cov-tree-popper .cov-node-name {
  color: var(--wb-ink);
  font-weight: 600;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 0 0 auto;
  width: 180px;
}
/* 第 5 层算子标签（蓝色） / 第 6 层产品目录标签（紫色） */
.cov-tree-popper .cov-prod {
  flex-shrink: 0;
  font-size: 10px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 8px;
  color: var(--wb-purple);
  background: var(--wb-purple-soft);
  white-space: nowrap;
}
.cov-tree-popper .cov-op {
  flex-shrink: 0;
  font-size: 10px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 8px;
  color: var(--wb-blue);
  background: var(--wb-blue-soft);
  white-space: nowrap;
}

/* 覆盖率指标：函数/行/分支（标签 + 数值），单行靠右，分级着色 */
.cov-tree-popper .cov-node-metrics {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-left: auto;
  flex: 0 0 auto;
}
.cov-tree-popper .cov-node-metrics .m {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 4px;
  height: 18px;
  padding: 0 6px;
  border-radius: 5px;
  background: var(--wb-code-bg);
  font-size: 11px;
  white-space: nowrap;
}
.cov-tree-popper .cov-node-metrics .mlab {
  flex-shrink: 0;
  font-style: normal;
  color: var(--wb-muted);
}
.cov-tree-popper .cov-node-metrics .m b {
  flex-shrink: 0;
  font-weight: 700;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--wb-ink);
}
/* 覆盖率分级着色：>=80 绿 / >=50 橙 / <50 红 */
.cov-tree-popper .cov-node-metrics .m.ok b { color: var(--wb-green); }
.cov-tree-popper .cov-node-metrics .m.mid b { color: var(--wb-orange); }
.cov-tree-popper .cov-node-metrics .m.low b { color: var(--wb-red); }
.cov-tree-popper .cov-node-metrics .m.none { flex: 0 0 auto; color: var(--wb-faint); }
</style>
