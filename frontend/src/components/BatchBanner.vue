<template>
  <div class="batch-banner" :class="status?.ready ? 'ready' : 'not-ready'">
    <template v-if="status?.current_batch_id">
      <span class="batch-chip">批次 {{ status.current_batch_id }}</span>
      <span class="batch-meta">
        业务日 {{ status.business_date }} · {{ status.business_timezone }} ·
        种子 {{ status.seed_version }} · {{ status.env }}
      </span>
      <span v-if="recId" class="batch-meta">核对号 {{ recId }}</span>
      <span class="batch-state">{{ status.ready ? '已就绪' : '未就绪' }}</span>
    </template>
    <template v-else>
      <span class="batch-state">尚无已发布批次，请先执行初始化流水线</span>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'

import { usePipelineStore } from '@/stores/pipeline'

const store = usePipelineStore()
const status = computed(() => store.status)
const recId = computed(() => store.status?.run?.reconciliation?.reconciliation_id)

onMounted(() => {
  if (!store.status) {
    void store.refresh()
  }
})
</script>

<style scoped>
.batch-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 16px;
}
.batch-banner.ready {
  background: #ecfdf3;
  border: 1px solid #abefc6;
  color: #067647;
}
.batch-banner.not-ready {
  background: #fffaeb;
  border: 1px solid #fedf89;
  color: #b54708;
}
.batch-chip {
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.7);
}
.batch-meta {
  opacity: 0.85;
}
.batch-state {
  margin-left: auto;
  font-weight: 600;
}
</style>
