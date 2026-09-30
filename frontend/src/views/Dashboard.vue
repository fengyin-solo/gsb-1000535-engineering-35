<template>
  <section class="page">
    <BatchBanner />
    <header class="page-head">
      <div>
        <h2>勘探数据概览</h2>
        <p class="page-desc">
          汇总各业务模块的关键指标，统计口径与当前批次台账、偏离清单一致；
          核对通过后卡片自动重算。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/ledger">模块台账核对</RouterLink>
        <RouterLink class="btn" to="/deviations">偏离清单</RouterLink>
        <RouterLink class="btn primary" to="/pipeline">流水线</RouterLink>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <p v-if="reconciliation" class="recon-line">
      核对号 {{ reconciliation.reconciliation_id }} ·
      台账校验和 {{ reconciliation.ledger_checksum }} ·
      偏离校验和 {{ reconciliation.deviation_checksum }} ·
      状态：{{ reconciliation.consistent ? '一致' : '不一致' }}
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th>业务模块</th>
          <th>本批新增</th>
          <th>待处理</th>
          <th>异常量</th>
          <th>台账总量（含既有批次）</th>
          <th>台账校验和</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in moduleRows" :key="row.name">
          <td>{{ row.label ?? row.name }}</td>
          <td>{{ row.created }}</td>
          <td>{{ row.pending }}</td>
          <td>{{ row.abnormal }}</td>
          <td>{{ row.ledger_total ?? '—' }}</td>
          <td class="mono">{{ row.checksum ?? '—' }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'
import BatchBanner from '@/components/BatchBanner.vue'

type Overview = {
  cards: { label: string; value: number }[]
  modules: {
    name: string
    label?: string
    created: number
    pending: number
    abnormal: number
    ledger_total?: number
    checksum?: string
  }[]
  batch_id?: string
  reconciliation_id?: string
  reconciliation?: {
    reconciliation_id: string
    consistent: boolean
    ledger_checksum: string
    deviation_checksum: string
    deviation_count: number
  }
}

const cards = ref<Overview['cards']>([])
const moduleRows = ref<Overview['modules']>([])
const reconciliation = ref<Overview['reconciliation']>()

onMounted(async () => {
  try {
    const payload = await fetchJson<Overview>('/api/overview')
    cards.value = payload.cards
    moduleRows.value = payload.modules
    reconciliation.value = payload.reconciliation
  } catch {
    cards.value = []
    moduleRows.value = []
  }
})
</script>

<style scoped>
.recon-line {
  font-size: 12px;
  color: #475467;
  margin: 4px 0 12px;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
</style>
