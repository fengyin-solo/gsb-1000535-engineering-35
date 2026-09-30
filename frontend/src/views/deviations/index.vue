<template>
  <section class="page">
    <BatchBanner />
    <header class="page-head">
      <div>
        <h2>偏离清单</h2>
        <p class="page-desc">
          跨日补录等偏离记录与汇总页、各模块台账同批次、同核对号。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新核对</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">当前批次</span>
        <strong class="stat-value">{{ doc.batch_id ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">核对号</span>
        <strong class="stat-value">{{ doc.reconciliation_id ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">偏离条数</span>
        <strong class="stat-value">{{ doc.total ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">业务时区</span>
        <strong class="stat-value">{{ doc.timezone ?? '—' }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>业务模块</th>
          <th>记录编号</th>
          <th>偏离类型</th>
          <th>数据日期</th>
          <th>业务日期</th>
          <th>登记时间（业务时区）</th>
          <th>台账核对</th>
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in doc.items ?? []" :key="`${item.module}-${item.entry_id}`">
          <td>{{ item.module_label }}</td>
          <td>{{ item.code }}</td>
          <td>{{ kindLabel(item.kind) }}</td>
          <td>{{ item.data_date }}</td>
          <td>{{ item.business_date }}</td>
          <td>{{ item.recorded_at }}</td>
          <td>{{ item.present_in_ledger ? '已在台账' : '台账缺失' }}</td>
          <td>{{ item.detail }}</td>
        </tr>
        <tr v-if="!(doc.items ?? []).length">
          <td colspan="8" class="empty-state">当前批次暂无偏离记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import BatchBanner from '@/components/BatchBanner.vue'
import { fetchJson } from '@/api/client'

type DeviationDoc = {
  batch_id: string | null
  reconciliation_id: string | null
  checksum?: string
  timezone?: string
  total: number
  items: {
    module: string
    module_label: string
    entry_id: number
    code: string
    kind: string
    data_date: string
    business_date: string
    recorded_at: string
    present_in_ledger: boolean
    detail: string
  }[]
}

const doc = ref<DeviationDoc>({ batch_id: null, reconciliation_id: null, total: 0, items: [] })
const errorMessage = ref('')

function kindLabel(kind: string): string {
  return kind === 'cross_day_late_entry' ? '跨日补录' : kind
}

async function reload() {
  errorMessage.value = ''
  try {
    doc.value = await fetchJson<DeviationDoc>('/api/deviations')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '偏离清单读取失败'
  }
}

onMounted(reload)
</script>
