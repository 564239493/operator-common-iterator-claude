<template>
  <div class="rnode" @click="pick">
    <button v-if="node.hasKids" class="tgl" :title="node.collapsed ? '展开' : '收起'" @click.stop="toggle">
      {{ node.collapsed ? '+' : '−' }}
    </button>
    <span v-else class="tgl-ph"></span>
    <!-- 层级标识：名称前、按层着色 -->
    <span class="layer" :style="{ color: node.color }">{{ node.tag }}</span>
    <!-- 节点名称：撑满剩余宽度，完整显示 -->
    <span class="name">{{ node.name }}</span>
    <!-- 数据：absolute 固定列；样式同 cov-node-metrics（胶囊 + 健康度分级着色） -->
    <span class="data">
      <span v-for="m in node.m" :key="m.label" class="m" :class="m.txt === '—' ? 'none' : hc(m.pct)">
        <em class="mlab">{{ m.label }}</em><b>{{ m.txt }}</b>
      </span>
    </span>
  </div>
  <ul v-if="node.hasKids && !node.collapsed" class="it-children">
    <li v-for="c in node.children" :key="c.key">
      <CoverageIndentNode :node="c" @select="pick" @toggle="toggle" />
    </li>
  </ul>
</template>

<script setup lang="ts">
const props = defineProps<{ node: any }>();
const emit = defineEmits<{ (e: 'select', p: { path: string[] }): void; (e: 'toggle', n: any): void }>();
// 健康度分级（同 cov-node-metrics）：>=80 绿 / >=50 橙 / <50 红
function hc(r: number) { return r >= 80 ? 'ok' : r >= 50 ? 'mid' : 'low'; }
function pick() { emit('select', { path: props.node.path }); }
function toggle() { emit('toggle', props.node); }
</script>

<style scoped>
/* 节点行：无边框无底色，单行 = 层级标识 + 名称（固定宽） + 数据（absolute 固定列）；14px 字体行高 36px */
.rnode { position: relative; display: flex; align-items: center; gap: 8px; padding: 5px 8px; cursor: pointer; min-height: 36px; }
.rnode:hover .name { color: var(--wb-blue); }
.tgl { width: 15px; height: 15px; border: 1px solid var(--wb-blue); border-radius: 4px; background: var(--wb-card);
  color: var(--wb-blue); font-weight: 700; font-size: 11px; line-height: 1; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; padding: 0; flex-shrink: 0; }
.tgl-ph { width: 15px; flex-shrink: 0; display: inline-block; }

/* 层级标识：名称前，每层一色（版本紫/算子库蓝/域绿/目录橙/算子红）；宽度随文字内容自适应 */
.layer { font-size: 11px; font-weight: 700; white-space: nowrap; flex-shrink: 0; }

/* 名称：固定宽度，超长省略；14px */
.name { width: 180px; flex-shrink: 0; font-weight: 650; font-size: 14px; color: var(--wb-ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* 数据：absolute 定位、left 固定（名称右缘 259px 之后的固定列）；
 * 样式参考 cov-node-metrics：胶囊底 + 标签灰 + 数值按健康度分级着色（>=80 绿 / >=50 橙 / <50 红） */
.data { position: absolute; left: 267px; top: 50%; transform: translateY(-50%);
  display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.data .m { flex: 0 0 auto; display: flex; align-items: center; gap: 4px; height: 18px;
  padding: 0 6px; border-radius: 5px; background: var(--wb-code-bg); font-size: 11px; white-space: nowrap; }
.data .mlab { flex-shrink: 0; font-style: normal; color: var(--wb-muted); }
.data .m b { flex-shrink: 0; font-weight: 700; font-size: 11px; font-variant-numeric: tabular-nums; color: var(--wb-ink); }
.data .m.ok b { color: var(--wb-green); }
.data .m.mid b { color: var(--wb-orange); }
.data .m.low b { color: var(--wb-red); }
.data .m.none b { color: var(--wb-faint); }

/* 引导线：竖向虚线在左，行前横向短线连接 */
.it-children { list-style: none; margin: 0; padding-left: 26px; position: relative; }
.it-children::before { content: ""; position: absolute; left: 9px; top: 0; bottom: 0; border-left: 1px dashed var(--wb-blue-line); }
.it-children li { position: relative; }
.it-children li::before { content: ""; position: absolute; left: -17px; top: 18px; width: 16px; border-top: 1px dashed var(--wb-blue-line); }
</style>