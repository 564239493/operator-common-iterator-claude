<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import { laneWidth } from '../board/flow-layout'
import RobotHead from './RobotHead.vue'
import { agentColor } from '../../composables/useEngineers'
import { STATUS_TEXT } from '../../api/types'

/** 第一屏底部：按流程顺序排布的机器人工程师带（点击选中看上方分列详情） */
const props = defineProps<{
  engineers: any[]
  selected: string | null
  round: number | null
  scrollLeft: number
  zoom: number
  viewport: number
}>()

const emit = defineEmits<{ (e: 'select', name: string | null): void; (e: 'scroll-x', left: number): void; (e: 'viewport', width: number): void }>()

const bandScroller = ref<HTMLElement>()
const cellWidth = computed(() => laneWidth(props.viewport, props.engineers.length, props.zoom))
const contentWidth = computed(() => 130 + props.engineers.length * cellWidth.value)
let observer: ResizeObserver | undefined
onMounted(() => {
  observer = new ResizeObserver(() => { if(bandScroller.value) emit('viewport', bandScroller.value.clientWidth) })
  if(bandScroller.value) observer.observe(bandScroller.value)
})
onBeforeUnmount(() => observer?.disconnect())
function scrollBand(delta: number) { bandScroller.value?.scrollBy({left:delta,behavior:'smooth'}) }
watch(() => [props.scrollLeft, props.zoom], async () => {
  await nextTick()
  if (bandScroller.value && Math.abs(bandScroller.value.scrollLeft - props.scrollLeft) > 1) bandScroller.value.scrollLeft = props.scrollLeft
})
function onScroll() { if (bandScroller.value) emit('scroll-x', bandScroller.value.scrollLeft) }
function clickable(eng: any): boolean {
  // 白名单：实际参与或证据待定的状态可点击；skipped/not_involved/未知一律不可点
  return ['pending', 'running', 'passed', 'rejected', 'unconfirmed'].includes(eng.runtime?.status || '')
}

function defBadge(eng: any): string {
  // 定义加载情况：找不到文件 / 文件存在但解析失败
  if (eng.definition_found === false) return '无定义'
  if (eng.load_error) return '加载失败'
  return ''
}

function onClick(eng: any) {
  if (!clickable(eng)) return
  emit('select', props.selected === eng.name ? null : eng.name)
}

function statusText(eng: any): string {
  return eng.runtime ? STATUS_TEXT[eng.runtime.status] || eng.runtime.status : '…'
}
</script>

<template>
  <div class="band-frame">
  <button class="band-scroll previous" aria-label="向左滚动智能体与流程" @click="scrollBand(-462)">‹</button>
  <button class="band-scroll next" aria-label="向右滚动智能体与流程" @click="scrollBand(462)">›</button>
  <div ref="bandScroller" class="band-viewport" @scroll="onScroll">
  <div :style="{ width: contentWidth * zoom + 'px', height: 150 * zoom + 'px' }">
  <div class="band" :style="{ width: contentWidth + 'px', transform: `scale(${zoom})` }">
    <div class="round-control"><strong>第 {{ round ?? "—" }} 轮</strong><small>角色与交接</small></div>
    <template v-for="(eng, idx) in engineers" :key="eng.name">

        <div
          class="engineer"
          :style="{ flexBasis: cellWidth + 'px', width: cellWidth + 'px' }"
          :class="{
            selected: selected === eng.name,
            rejected: eng.runtime?.status === 'rejected',
            disabled: !clickable(eng),
          }"
          @click="onClick(eng)"
        >
          <RobotHead
            :color="agentColor(eng.color)"
            :status="eng.runtime?.status || 'pending'"
            :agent="eng.name"
            :size="56"
            :active="selected === eng.name || eng.runtime?.status === 'running'"
          />
          <div class="name">{{ eng.name }}</div>
          <div class="role">{{ eng.role || '流程外' }}</div>
          <div class="status" :data-status="eng.runtime?.status">
            {{ statusText(eng) }}
            <span v-if="eng.runtime?.inferred" class="inferred-badge" :title="'由产物与 history 推导（规则集 ' + (eng.runtime?.ruleset || '?') + '）'">推导</span>
          </div>
          <div v-if="defBadge(eng)" class="def-badge"
               :title="eng.load_error ? ('定义文件解析失败：' + eng.load_error) : '未找到该角色的定义文件，以下为流程固定角色'">
            {{ defBadge(eng) }}
          </div>
        </div>


    </template>
  </div>
  </div>
  </div>
  </div>
</template>

<style scoped>
.band {
  display: flex;
  align-items: flex-start;
  gap: 2px;
  overflow-x: auto;
  padding: 10px 14px 12px;
  background: var(--wb-card);
  border-top: 1px solid var(--wb-line);
}
.engineer {
  flex: 1 1 0;
  min-width: 88px;
  text-align: center;
  padding: 6px 4px 8px;
  border-radius: 10px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}
.engineer:hover { background: var(--wb-hover); }
/* 未参与/跳过：不可点击，视觉降权 */
.engineer.disabled { cursor: default; }
.engineer.disabled:hover { background: transparent; }
.engineer.selected { background: var(--wb-blue-soft); border-color: var(--wb-blue-line); box-shadow: inset 0 -3px var(--wb-blue); }
.engineer.rejected { border-color: var(--wb-red-line); }
.name {
  margin-top: 2px;
  font-size: 11px;
  font-weight: 600;
  color: var(--wb-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.role { font-size: 10px; color: var(--wb-muted); margin-top: 1px; }
.status { font-size: 10px; margin-top: 3px; }
.status[data-status='passed'] { color: var(--wb-green); }
.status[data-status='rejected'] { color: var(--wb-red); }
.status[data-status='running'] { color: var(--wb-orange); }
.status[data-status='unconfirmed'] { color: #b0a06a; } /* 灰黄：证据不足 ≠ 待运行 */
.status[data-status='pending'] { color: var(--wb-muted); }
.status[data-status='skipped'], .status[data-status='not_involved'] { color: var(--wb-faint); }
.inferred-badge {
  display: inline-block;
  margin-left: 3px;
  padding: 0 4px;
  font-size: 9px;
  line-height: 14px;
  border-radius: 6px;
  background: var(--wb-line);
  color: var(--wb-muted);
  vertical-align: 1px;
}
/* 定义加载情况标记：无定义 / 加载失败（不伪装成功） */
.def-badge {
  margin-top: 2px;
  padding: 0 5px;
  font-size: 9px;
  line-height: 14px;
  border-radius: 6px;
  background: var(--wb-red-soft);
  color: var(--wb-red);
  display: inline-block;
}
.arrow {
  flex: 0 0 auto;
  align-self: center;
  color: var(--wb-faint);
  font-size: 14px;
  margin-top: 16px;
}
@media (max-width: 600px) {
  .engineer { min-width: 76px; }
  .arrow { font-size: 12px; }
}
.round-control { position: sticky; left: 0; z-index: 2; flex: 0 0 145px; align-self: stretch; display: flex; flex-direction: column; justify-content: center; gap: 6px; padding: 8px 14px; background: var(--wb-card); border-right: 1px solid var(--wb-line); margin-right: 10px; }
.round-control label{font-size:11px;color:var(--wb-muted)}.round-control select{width:116px;padding:6px;border:1px solid var(--wb-blue-line);border-radius:6px;color:var(--wb-blue);background:var(--wb-card);font:inherit}.round-control small{font-size:9px;color:var(--wb-faint)}.engineer.disabled{filter:grayscale(1);opacity:.4}
.band-viewport{overflow-x:auto;overflow-y:hidden;scrollbar-width:none;min-width:0}.band-viewport::-webkit-scrollbar{display:none}
.band{height:150px;padding:0;gap:0;overflow:visible;transform-origin:top left;align-items:stretch;border:0}
.band .round-control{position:static;flex:0 0 130px;width:130px;box-sizing:border-box;margin:0;padding:10px;}
.band .round-control select{width:110px}.band .round-control small{font-size:9px}
.band .engineer{flex:0 0 154px;width:154px;min-width:154px;box-sizing:border-box;border-radius:0;padding:12px 4px 8px;border-left:1px solid var(--wb-line-soft)}
.band-frame{position:relative;width:100%;min-width:0}
.band-scroll{position:absolute;top:55%;transform:translateY(-50%);z-index:5;width:24px;height:40px;border:1px solid var(--wb-blue-line);border-radius:6px;background:var(--wb-card);color:var(--wb-blue);font-size:26px;box-shadow:var(--wb-shadow)}.band-scroll.previous{left:2px}.band-scroll.next{right:2px}
.band .round-control{border-right:0}.band .engineer{border-right:0;border-top:0;border-bottom:0;border-left:1px solid var(--wb-line);}
</style>
