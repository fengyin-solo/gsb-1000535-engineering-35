<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>勘探数据概览</h2>
        <p class="page-desc">汇总各业务模块的关键指标，汇总页、模块台账与偏离清单统一按同一发布批次核对。</p>
      </div>
      <div class="page-actions">
        <span v-if="batch" class="batch-tag" :class="{ 'batch-bad': !matched }">
          当前批次：{{ batch }} · {{ businessDate }} · {{ timezone }}
        </span>
        <span v-else class="batch-tag batch-bad">流水线尚未发布批次（数据未就绪）</span>
        <span v-if="matched" class="batch-tag batch-ok">三方核对一致</span>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr><th>业务模块</th><th>本批次新增</th><th>待处理</th><th>异常量</th><th>跨日补录</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.label ?? row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
          <td>{{ row.backfill ?? 0 }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">偏离清单（{{ deviations.length }}）</h3>
    <table class="data-table">
      <thead>
        <tr><th>模块</th><th>业务编号</th><th>偏离类型</th><th>业务日期</th><th>补录时间</th><th>状态</th><th>批次</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in deviations" :key="`${item.module}-${item.entry_id}`">
          <td>{{ item.module_label ?? item.module }}</td>
          <td>{{ item.code ?? '—' }}</td>
          <td>{{ item.kind }}</td>
          <td>{{ item.business_date ?? '—' }}</td>
          <td>{{ item.recorded_at ?? '—' }}</td>
          <td>{{ item.status ?? '—' }}</td>
          <td>{{ item.batch_id }}</td>
        </tr>
        <tr v-if="!deviations.length">
          <td colspan="7" class="empty-state">当前批次暂无偏离记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'

type ModuleRow = {
  name: string
  label?: string
  created: number
  pending: number
  abnormal: number
  backfill?: number
}

type Overview = {
  cards: { label: string; value: number }[]
  modules: ModuleRow[]
  batch_id: string | null
  timezone?: string
  business_date?: string
  reconciliation?: { matched: boolean; checked_at?: string } | null
}

type Deviation = {
  module: string
  module_label?: string
  entry_id: number
  code?: string
  kind: string
  business_date?: string
  recorded_at?: string
  status?: string
  batch_id: string
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<ModuleRow[]>([])
const deviations = ref<Deviation[]>([])
const batchId = ref<string | null>(null)
const timezone = ref<string>('')
const businessDate = ref<string>('')
const matched = ref(false)
const errorMessage = ref('')

const batch = computed(() => batchId.value)

async function load() {
  errorMessage.value = ''
  try {
    const overview = await fetchJson<Overview>('/api/overview')
    cards.value = overview.cards
    moduleRows.value = overview.modules
    batchId.value = overview.batch_id
    timezone.value = overview.timezone ?? ''
    businessDate.value = overview.business_date ?? ''
    matched.value = overview.reconciliation?.matched === true

    if (batchId.value) {
      const payload = await fetchJson<{ items: Deviation[] }>('/api/pipeline/deviations')
      deviations.value = payload.items
    } else {
      deviations.value = []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '概览数据读取失败'
    cards.value = []
    moduleRows.value = []
  }
}

onMounted(load)
</script>
