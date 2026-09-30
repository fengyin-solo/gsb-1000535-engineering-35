<template>
  <section class="page">
    <BatchBanner />
    <header class="page-head">
      <div>
        <h2>模块台账核对</h2>
        <p class="page-desc">
          18 个业务模块的台账都必须落在同一批次、同一核对号上；校验和不一致即视为污染。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新台账</button>
      </div>
    </header>

    <table class="data-table">
      <thead>
        <tr>
          <th>业务模块</th>
          <th>台账总量</th>
          <th>本批新增</th>
          <th>待处理</th>
          <th>异常量</th>
          <th>批次</th>
          <th>核对号</th>
          <th>台账校验和</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in modules" :key="row.module">
          <td>{{ row.label }}</td>
          <td>{{ row.total }}</td>
          <td>{{ row.stamp?.created ?? 0 }}</td>
          <td>{{ row.stamp?.pending ?? 0 }}</td>
          <td>{{ row.stamp?.abnormal ?? 0 }}</td>
          <td>{{ row.stamp?.batch_id ?? '—' }}</td>
          <td>{{ row.stamp?.reconciliation_id ?? '—' }}</td>
          <td class="mono">{{ row.stamp?.checksum ?? '—' }}</td>
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

import { fetchJson } from '@/api/client'
import BatchBanner from '@/components/BatchBanner.vue'

type Stamp = {
  batch_id: string
  reconciliation_id: string
  created: number
  pending: number
  abnormal: number
  checksum: string
}
type LedgerOverview = {
  batch_id: string | null
  reconciliation_id: string | null
  modules: { module: string; label: string; total: number; stamp: Stamp | null }[]
}

const modules = ref<LedgerOverview['modules']>([])
const errorMessage = ref('')

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await fetchJson<LedgerOverview>('/api/ledger')
    modules.value = payload.modules
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模块台账读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
</style>
