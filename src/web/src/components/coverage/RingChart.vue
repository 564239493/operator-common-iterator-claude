<script setup lang="ts">
import { computed } from 'vue';
const props = withDefaults(defineProps<{ name: string; rate: number; covered?: number; total?: number; size?: number }>(), { size: 110 });
function rateHex(r: number) { return r >= 80 ? '#159b78' : r >= 50 ? '#df8a27' : '#da5961' }
const R = 42;
const C = 2 * Math.PI * R;
// 数字清晰：整数直接显示，否则保留 1 位小数
const display = computed(() => {
  const r = Number(props.rate) || 0;
  const rounded = Math.round(r * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
});
const shortLabel = computed(() => props.name.replace('覆盖率', ''));
</script>

<template>
  <div class="ring">
    <svg :width="size" :height="size" viewBox="0 0 110 110">
      <circle cx="55" cy="55" :r="R" fill="none" stroke="#eef2f7" stroke-width="10" />
      <circle cx="55" cy="55" :r="R" fill="none" :stroke="rateHex(rate)" stroke-width="10" stroke-linecap="round"
        :stroke-dasharray="C" :stroke-dashoffset="C * (1 - Math.min(100, rate) / 100)"
        transform="rotate(-90 55 55)" />
      <text x="55" y="55" text-anchor="middle" dominant-baseline="central" :fill="rateHex(rate)" font-weight="700"
        font-family="'JetBrains Mono', Consolas, monospace">
        <tspan font-size="20">{{ display }}</tspan><tspan font-size="12" dy="-6">%</tspan>
      </text>
    </svg>
    <div class="ring-lbl">{{ shortLabel }}</div>
    <div class="tip">
      <div class="tip-title">{{ name }}</div>
      <div class="tip-line">覆盖率 <b :style="{ color: rateHex(rate) }">{{ display }}%</b></div>
      <div class="tip-line" v-if="total != null">已覆盖 <b>{{ covered }}/{{ total }}</b></div>
    </div>
  </div>
</template>

<style scoped>
.ring { position: relative; text-align: center; }
.ring-lbl { font-size: 12px; color: var(--wb-muted); margin-top: 4px; font-weight: 500; }
.tip {
  position: absolute; left: 50%; bottom: calc(100% + 10px); transform: translateX(-50%);
  background: var(--wb-ink); color: #fff; padding: 8px 12px; border-radius: 8px; font-size: 12px;
  white-space: nowrap; text-align: left; opacity: 0; visibility: hidden; pointer-events: none;
  transition: opacity .15s, visibility .15s; z-index: 20; box-shadow: 0 4px 12px rgba(0,0,0,.15);
}
.tip::after { content: ""; position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
  border: 5px solid transparent; border-top-color: var(--wb-ink); }
.ring:hover .tip { opacity: 1; visibility: visible; }
.tip-title { font-weight: 600; margin-bottom: 2px; }
.tip-line { color: rgba(255,255,255,.85); }
.tip-line b { font-weight: 600; }
</style>
