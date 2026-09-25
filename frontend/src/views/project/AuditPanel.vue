<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Search } from 'lucide-vue-next'
import { api, ApiError } from '../../api'
import type { Asset, AuditEvent, Episode, Project, Timeline } from '../../types'
const props = defineProps<{ project: Project; canAudit: boolean }>()
const logs = ref<AuditEvent[]>([]); const assets = ref<Asset[]>([]); const selectedAssetId = ref(''); const lineage = ref<unknown>(null); const error = ref('')
const root = computed(() => `/projects/${props.project.id}`)
async function load() {
  try {
    if (props.canAudit) logs.value = await api.get(`${root.value}/audit`)
    const [allAssets, episodes] = await Promise.all([
      api.get<Asset[]>(`${root.value}/assets`),
      api.get<Episode[]>(`${root.value}/episodes`),
    ])
    const finalIds = new Set<string>()
    for (const episode of episodes) {
      try {
        const timeline = await api.get<Timeline>(`${root.value}/episodes/${episode.id}/timeline`)
        if (timeline.final_asset_id) finalIds.add(timeline.final_asset_id)
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.code !== 'RESOURCE_NOT_FOUND') throw cause
      }
    }
    assets.value = allAssets.filter(asset => finalIds.has(asset.id))
    if (selectedAssetId.value && !finalIds.has(selectedAssetId.value)) {
      selectedAssetId.value = ''
      lineage.value = null
    }
  } catch (cause) { error.value = (cause as Error).message }
}
async function loadLineage() { if (!selectedAssetId.value) return; try { lineage.value = await api.get(`${root.value}/assets/${selectedAssetId.value}/lineage`) } catch (cause) { error.value = (cause as Error).message } }
onMounted(load)
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Audit & Lineage</div><h1>审计与生成溯源</h1><p>沿成片、时间线、镜头、素材和生成任务追踪制作依据。</p></div></div><div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div>
  <section class="panel panel-pad" style="margin-bottom:18px"><h2>成片溯源</h2><div class="row"><select v-model="selectedAssetId" aria-label="选择成片素材"><option value="" disabled>选择最终 MP4 素材</option><option v-for="asset in assets" :key="asset.id" :value="asset.id">{{ asset.id.slice(0,12) }} · {{ asset.duration?.toFixed(1) }} 秒</option></select><button class="btn" @click="loadLineage"><Search :size="15" />查看链路</button></div><pre v-if="lineage" class="subtle-panel mono" style="padding:15px;white-space:pre-wrap;overflow:auto;max-height:350px;margin-top:14px">{{ JSON.stringify(lineage,null,2) }}</pre></section>
  <section v-if="canAudit" class="panel panel-pad"><div class="between"><h2>审计日志</h2><span class="muted">最近 {{ logs.length }} 条</span></div><div v-if="logs.length" class="table-wrap"><table class="data-table"><thead><tr><th>时间</th><th>执行者</th><th>动作</th><th>对象</th><th>结果</th><th>Trace</th></tr></thead><tbody><tr v-for="log in logs" :key="log.id"><td>{{ new Date(log.time).toLocaleString('zh-CN') }}</td><td>{{ log.actor_type }}</td><td>{{ log.action }}</td><td class="mono">{{ log.resource_type }} · {{ log.resource_id?.slice(0,8) }}</td><td>{{ log.result }}</td><td class="mono">{{ log.trace_id.slice(0,8) }}</td></tr></tbody></table></div><div v-else class="empty"><div><strong>暂无审计记录</strong><p>关键业务动作会在这里留下可检索的证据。</p></div></div></section>
  <div v-else class="notice">仅项目所有者可读取审计日志。</div>
</template>
