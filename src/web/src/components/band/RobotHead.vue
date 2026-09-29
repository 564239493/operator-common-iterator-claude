<script setup lang="ts">
import { computed } from 'vue'

/**
 * 纯 SVG 机器人头部（设计更新 1：机器人化、只保留头部）。
 * 按角色名称固定造型；variant 保留为无角色时的兼容回退。
 * 颜色走 CSS 变量，明暗主题自适应。
 */
const props = withDefaults(
  defineProps<{
    color?: string
    status?: string
    variant?: number
    agent?: string
    size?: number
    active?: boolean
  }>(),
  { color: '#2774ed', status: 'pending', variant: 0, size: 72, active: false },
)

const portraits: Record<string, number> = {
  'scene-scanner': 0,
  'case-executor': 1,
  'case-generator': 2,
  'constraint-extractor': 3,
  'source-analyst': 4,
  'constraint-supplementer': 5,
  'constraint-checker': 6,
  'constraint-repairer': 7,
  'failure-analyst': 8,
  'constraint-updater': 9,
  'quality-reviewer': 10,
  'prompt-optimizer': 11,
}
const portrait = computed(() => portraits[props.agent || ''] ?? props.variant)

const eyeColor = computed(() => {
  switch (props.status) {
    case 'passed':
      return '#159b78'
    case 'rejected':
      return '#da5961'
    case 'running':
      return '#df8a27'
    case 'skipped':
    case 'not_involved':
      return '#aab2be'
    case 'unconfirmed':
      return '#b0a06a'
    default:
      return '#7d90bd'
  }
})

const dimmed = computed(() => props.status === 'skipped' || props.status === 'not_involved')
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 80 80"
    class="robot-head"
    :class="{ dimmed, active, running: status === 'running' }"
    role="img"
  >
    <!-- 天线 -->
    <g v-if="portrait === 0">
      <path d="M29 12 Q40 2 51 12 M34 16 Q40 10 46 16" fill="none" :stroke="color" stroke-width="2" stroke-linecap="round" opacity="0.65" />
      <line x1="40" y1="14" x2="40" y2="26" :stroke="color" stroke-width="3" stroke-linecap="round" />
      <circle cx="40" cy="11" r="4" :fill="color" class="antenna-dot" />
    </g>
    <!-- 耳机 -->
    <g v-if="portrait === 1">
      <path d="M18 42 v-6 a22 22 0 0 1 44 0 v6" fill="none" :stroke="color" stroke-width="3.5" stroke-linecap="round" />
      <rect x="13" y="40" width="8" height="14" rx="3.5" :fill="color" opacity="0.85" />
      <rect x="59" y="40" width="8" height="14" rx="3.5" :fill="color" opacity="0.85" />
    </g>
    <!-- 散热孔 -->
    <g v-if="portrait === 2">
      <line x1="30" y1="12" x2="30" y2="20" :stroke="color" stroke-width="3" stroke-linecap="round" opacity="0.7" />
      <line x1="40" y1="10" x2="40" y2="20" :stroke="color" stroke-width="3" stroke-linecap="round" opacity="0.9" />
      <line x1="50" y1="12" x2="50" y2="20" :stroke="color" stroke-width="3" stroke-linecap="round" opacity="0.7" />
    </g>

    <!-- 每个角色的轮廓特征，在小尺寸下也不只依赖颜色区分 -->
    <g :stroke="color" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none">
      <path v-if="portrait === 3" d="M26 25 V18 H34 M46 18 H54 V25" />
      <path v-if="portrait === 4" d="M28 24 L23 17 L28 10 M52 24 L57 17 L52 10" />
      <g v-if="portrait === 5">
        <rect x="8" y="36" width="8" height="16" rx="3" />
        <path d="M64 36 H70 V40 H75 V48 H70 V52 H64" />
      </g>
      <path v-if="portrait === 6" d="M29 24 V16 H51 V24 M35 16 V12 H45 V16" />
      <path v-if="portrait === 7" d="M21 30 V24 Q21 12 33 12 H47 Q59 12 59 24 V30 M34 13 V23 M46 13 V23" />
      <path v-if="portrait === 8" d="M18 38 H11 V28 H18 M40 24 V15 H50" />
      <g v-if="portrait === 9">
        <path d="M26 16 H52 L47 11 M54 21 H28 L33 26" />
      </g>
      <path v-if="portrait === 10" d="M31 12 L40 9 L49 12 V19 Q47 24 40 28 Q33 24 31 19 Z M36 17 L39 20 L44 15" class="face" />
      <g v-if="portrait === 11">
        <path d="M40 8 L49 17 L40 26 L31 17 Z M25 17 H20 M55 17 H60 M40 4 V8" />
      </g>
    </g>

    <!-- 头部主体 -->
    <rect
      x="16"
      y="24"
      width="48"
      height="40"
      rx="12"
      class="face"
      :stroke="color"
      :stroke-width="active ? 3 : 2"
    />
    <!-- 额头装饰条 -->
    <rect x="26" y="30" width="28" height="4" rx="2" :fill="color" opacity="0.4" />

    <!-- 眼睛 -->
    <g class="eyes">
      <circle cx="31" cy="44" r="4.5" :fill="eyeColor" class="eye" />
      <circle cx="49" cy="44" r="4.5" :fill="eyeColor" class="eye" />
    </g>

    <!-- 光学配件保留眼睛的状态色，嘴部表情保持原有语义 -->
    <g :stroke="color" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none">
      <g v-if="portrait === 3">
        <rect x="23" y="37" width="34" height="14" rx="5" />
        <path d="M25 40 H55" opacity="0.45" />
      </g>
      <g v-if="portrait === 4">
        <rect x="23" y="37" width="14" height="14" rx="3" />
        <rect x="43" y="37" width="14" height="14" rx="3" />
        <path d="M37 42 H43 M17 40 H23 M57 40 H63" />
      </g>
      <g v-if="portrait === 6">
        <circle cx="31" cy="44" r="8" />
        <circle cx="49" cy="44" r="8" />
        <path d="M39 43 H41 M17 41 H23 M57 41 H63" />
      </g>
      <g v-if="portrait === 7">
        <path d="M20 37 H60 L57 49 H23 Z M40 38 V48" />
      </g>
      <g v-if="portrait === 8">
        <circle cx="49" cy="44" r="9" />
        <path d="M56 51 L63 59" stroke-width="3.5" />
      </g>
      <path v-if="portrait === 1" d="M64 49 V56 Q64 59 60 59 H54" />
      <path v-if="portrait === 5" d="M20 54 H25 M22.5 51.5 V56.5" />
      <path v-if="portrait === 9" d="M22 39 L26 35 M54 35 L58 39" />
      <path v-if="portrait === 10" d="M23 38 L31 36 M49 36 L57 38" />
      <path v-if="portrait === 11" d="M21 43 L24 39 L27 43 L24 47 Z M53 43 L56 39 L59 43 L56 47 Z" opacity="0.6" />
    </g>

    <!-- 嘴部：状态驱动 -->
    <g v-if="status === 'rejected'">
      <line x1="33" y1="53" x2="47" y2="57" stroke="#da5961" stroke-width="3" stroke-linecap="round" />
    </g>
    <g v-else-if="status === 'passed'">
      <path d="M32 53 q8 6 16 0" fill="none" stroke="#159b78" stroke-width="3" stroke-linecap="round" />
    </g>
    <g v-else>
      <line x1="33" y1="55" x2="47" y2="55" :stroke="eyeColor" stroke-width="3" stroke-linecap="round" opacity="0.8" />
    </g>

    <!-- 底部小底座（仅头部，无身体） -->
    <rect x="30" y="64" width="20" height="5" rx="2.5" :fill="color" opacity="0.3" />
  </svg>
</template>

<style scoped>
.robot-head { display: block; transition: filter 0.2s, transform 0.2s; }
.face { fill: var(--robot-face); }
.robot-head.dimmed { opacity: 0.5; filter: grayscale(0.7); }
.robot-head.active { filter: drop-shadow(0 2px 6px rgba(39, 116, 237, 0.35)); transform: translateY(-2px); }
.running .eye { animation: scan 1.2s ease-in-out infinite; }
.running .antenna-dot { animation: blink 0.9s ease-in-out infinite; }
@keyframes scan {
  0%, 100% { transform: translateX(0); }
  50% { transform: translateX(2px); }
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}
</style>
