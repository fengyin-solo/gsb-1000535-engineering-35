/** 流水线批次状态：汇总页、台账、偏离清单共用同一份批次/核对信息。 */
import { defineStore } from 'pinia'

import { fetchJson } from '@/api/client'

export type PipelineStatus = {
  ready: boolean
  env: string
  business_timezone: string
  cache_backend: string
  queue_backend: string
  current_batch_id: string | null
  seed_version: string | null
  business_date: string | null
  published_at: string | null
  run: {
    ready: boolean
    stages: Record<string, { status: string; detail: string; at: string | null }>
    reconciliation?: { reconciliation_id: string; consistent: boolean; deviation_count: number }
    batch?: { batch_id: string; late_entries: number; sample_rows: number }
  } | null
  history: { batch_id: string; business_date: string; published_at: string }[]
}

export const usePipelineStore = defineStore('session', {
  state: () => ({
    status: null as PipelineStatus | null,
    loading: false,
    error: '',
  }),
  actions: {
    async refresh() {
      this.loading = true
      this.error = ''
      try {
        this.status = await fetchJson<PipelineStatus>('/api/pipeline/status')
      } catch (error) {
        this.error = error instanceof Error ? error.message : '流水线状态读取失败'
      } finally {
        this.loading = false
      }
    },
  },
})
