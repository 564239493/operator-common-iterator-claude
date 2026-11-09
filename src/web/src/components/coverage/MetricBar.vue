<script setup lang="ts">
defineProps<{ name: string; rate: number; detail?: string }>()
function rateHex(r: number) { return r >= 80 ? '#159b78' : r >= 50 ? '#df8a27' : '#da5961' }
</script>

<template>
  <div class="metric">
    <div class="mr">
      <span>{{ name }}</span>
      <b :style="{ color: rateHex(rate) }">{{ rate }}%<span v-if="detail" class="detail"> · {{ detail }}</span></b>
    </div>
    <div class="bar"><i :style="{ width: Math.min(100, rate) + '%', background: rateHex(rate) }"></i></div>
  </div>
</template>

<style scoped>
.metric { margin: 6px 0; }
.mr { display: flex; justify-content: space-between; font-size: 12px; color: var(--wb-muted); margin-bottom: 2px; }
.mr b { color: var(--wb-ink); font-family: "JetBrains Mono", monospace; font-weight: 600; }
.mr .detail { color: var(--wb-faint); font-weight: 400; }
.bar { height: 7px; border-radius: 4px; background: #edf1f7; overflow: hidden; }
.bar > i { display: block; height: 100%; border-radius: 4px; }
</style>
