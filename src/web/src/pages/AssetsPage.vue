<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="assets">
    <el-page-header content="资产页面" @back="router.back()" />
    <el-empty description="资产页面（Agent / Skill / 知识库结构与调用关系）将在第二步实现">
      <el-button type="primary" @click="router.push('/')">返回运行时页面</el-button>
    </el-empty>
  </div>
</template>

<style scoped>
.assets { padding: 20px 26px; }
</style>
