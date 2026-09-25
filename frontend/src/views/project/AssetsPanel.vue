<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Film, Image, Music2, RefreshCcw } from 'lucide-vue-next'
import { api } from '../../api'
import type { Asset, Project } from '../../types'
const props = defineProps<{ project: Project; eventRevision: number }>()
const assets = ref<Asset[]>([]); const urls = ref<Record<string,string>>({}); const error = ref('')
async function load() { try { assets.value = await api.get(`/projects/${props.project.id}/assets`); for (const asset of assets.value) if (!urls.value[asset.id]) urls.value[asset.id] = await api.blob(`/projects/${props.project.id}/assets/${asset.id}/content`) } catch (cause) { error.value = (cause as Error).message } }
watch(() => props.eventRevision, load); onMounted(load); onBeforeUnmount(() => Object.values(urls.value).forEach(URL.revokeObjectURL))
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Asset Library</div><h1>素材库</h1><p>所有生成资源经过格式验证后存入项目对象存储，并保留任务来源。</p></div><button class="btn" @click="load"><RefreshCcw :size="15" />刷新</button></div><div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div>
  <div v-if="assets.length" class="grid-three"><div v-for="asset in assets" :key="asset.id" class="panel" style="overflow:hidden"><div class="shot-thumb" style="height:190px"><img v-if="asset.mime.startsWith('image')" :src="urls[asset.id]" alt="生成素材" /><video v-else-if="asset.mime.startsWith('video')" :src="urls[asset.id]" controls style="width:100%;height:100%"></video><audio v-else-if="asset.mime.startsWith('audio')" :src="urls[asset.id]" controls style="width:90%"></audio><Image v-else :size="30" /></div><div style="padding:16px"><div class="between"><div class="row"><Image v-if="asset.mime.startsWith('image')" :size="15" /><Film v-else-if="asset.mime.startsWith('video')" :size="15" /><Music2 v-else :size="15" /><strong style="font-size:13px">{{ asset.mime }}</strong></div><span class="status status-ready">{{ asset.status }}</span></div><p class="mono muted" style="margin:9px 0 0">{{ asset.id.slice(0,13) }} · {{ (asset.size / 1024 / 1024).toFixed(2) }} MB</p><small v-if="asset.duration">{{ asset.duration.toFixed(1) }} 秒</small></div></div></div><div v-else class="empty"><div><strong>素材库为空</strong><p>生成图像、视频或配音后，资源会出现在这里。</p></div></div>
</template>
