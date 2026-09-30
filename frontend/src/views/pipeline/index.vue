<template>
  <section class="page">
    <BatchBanner />
    <header class="page-head">
      <div>
        <h2>初始化 / 构建 / 部署流水线</h2>
        <p class="page-desc">
          阶段固定：preflight → migrate → generate → load → reconcile → verify → publish。
          重复执行幂等，已成功阶段从检查点跳过，未成功阶段不会被标记为就绪。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="busy" @click="runDeploy">
          {{ busy ? '提交中…' : '按业务日执行/续跑' }}
        </button>
        <button class="btn" type="button" :disabled="busy" @click="recompute">重新核对并重算</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="runDeploy">
      <label class="filter-item">
        <span>业务日期（{{ status?.business_timezone }}）</span>
        <input v-model="businessDate" type="date" />
      </label>
      <label class="filter-item">
        <span>种子版本</span>
        <select v-model="seedVersion">
          <option>v1</option>
          <option>v2</option>
          <option>v3</option>
        </select>
      </label>
    </form>

    <div v-if="message" class="page-foot">
      <span :class="messageOk ? '' : 'error-text'">{{ message }}</span>
    </div>

    <table class="data-table">
      <thead>
        <tr><th>阶段</th><th>状态</th><th>说明</th><th>完成时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="stage in stageOrder" :key="stage">
          <td>{{ stage }}</td>
          <td>{{ stages[stage]?.status ?? 'pending' }}</td>
          <td>{{ stages[stage]?.detail ?? '' }}</td>
          <td>{{ stages[stage]?.at ?? '' }}</td>
        </tr>
      </tbody>
    </table>

    <h3 style="margin-top:18px">部署依赖</h3>
    <table class="data-table">
      <thead>
        <tr><th>依赖</th><th>后端</th><th>环境</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr>
          <td>缓存（汇总统计）</td>
          <td>{{ status?.cache_backend }}</td>
          <td>{{ status?.env }}</td>
          <td>部署前 preflight 探测，不可用即阻断发布</td>
        </tr>
        <tr>
          <td>队列（台账装载作业）</td>
          <td>{{ status?.queue_backend }}</td>
          <td>{{ status?.env }}</td>
          <td>断点续跑：作业复位后从检查点再次提交</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import BatchBanner from '@/components/BatchBanner.vue'
import { usePipelineStore } from '@/stores/pipeline'

const stageOrder = ['preflight', 'migrate', 'generate', 'load', 'reconcile', 'verify', 'publish']
const store = usePipelineStore()
const businessDate = ref(new Date().toISOString().slice(0, 10))
const seedVersion = ref('v3')
const busy = ref(false)
const message = ref('')
const messageOk = ref(true)

const status = computed(() => store.status)
const stages = computed(() => store.status?.run?.stages ?? {})

async function refresh() {
  await store.refresh()
  if (store.status?.business_date) {
    businessDate.value = store.status.business_date
  }
  if (store.status?.seed_version) {
    seedVersion.value = store.status.seed_version
  }
}

async function runDeploy() {
  busy.value = true
  message.value = ''
  try {
    const response = await request('/api/pipeline/deploy', {
      method: 'POST',
      body: JSON.stringify({ business_date: businessDate.value, seed_version: seedVersion.value }),
    })
    const payload = await response.json()
    if (!response.ok) {
      messageOk.value = false
      message.value = `发布未成功：${payload.detail ?? response.statusText}`
    } else {
      messageOk.value = true
      message.value = `批次 ${payload.batch_id} 已就绪`
    }
    await refresh()
  } catch (error) {
    messageOk.value = false
    message.value = error instanceof Error ? error.message : '流水线执行失败'
  } finally {
    busy.value = false
  }
}

async function recompute() {
  busy.value = true
  message.value = ''
  try {
    const response = await request('/api/pipeline/reconcile', { method: 'POST' })
    if (!response.ok) {
      messageOk.value = false
      message.value = '核对重算失败'
    } else {
      messageOk.value = true
      message.value = '核对完成，汇总统计已重算'
    }
    await refresh()
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
</script>
