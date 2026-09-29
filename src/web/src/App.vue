<template>
  <router-view />
</template>

<style>
* { box-sizing: border-box; }
html, body, #app {
  margin: 0;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--wb-bg);
  color: var(--wb-ink);
  font-size: 14px;
  line-height: 1.6;
}
button { font: inherit; cursor: pointer; }
button:focus-visible, a:focus-visible, [tabindex]:focus-visible {
  outline: 3px solid #77aaff;
  outline-offset: 2px;
}
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: var(--wb-faint); border-radius: 4px; }
::-webkit-scrollbar-track { background: transparent; }
</style>
