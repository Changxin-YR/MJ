<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Film, Play, RefreshCcw } from 'lucide-vue-next'
import { api, ApiError } from '../../api'
import StatusPill from '../../components/StatusPill.vue'
import type { Episode, Project, Timeline } from '../../types'
defineOptions({ inheritAttrs: false })
const props = defineProps<{ project: Project; canEdit: boolean; eventRevision: number }>()
const root = computed(() => `/projects/${props.project.id}`)
const episodes = ref<Episode[]>([]); const episodeId = ref(''); const timeline = ref<Timeline | null>(null); const videoUrl = ref(''); const busy = ref(false); const error = ref(''); const notice = ref(''); const loading = ref(true)
const duration = computed(() => Math.max(0, ...(timeline.value?.items || []).map(i => i.start_seconds + i.duration_seconds)))
let loadedAssetId = ''; let revision = 0; let disposed = false
function releaseVideo() { if (videoUrl.value) URL.revokeObjectURL(videoUrl.value); videoUrl.value = ''; loadedAssetId = '' }
async function loadEpisodes() { try { episodes.value = await api.get(`${root.value}/episodes`); if (!episodeId.value && episodes.value.length) episodeId.value = episodes.value[0]!.id; await load() } catch (cause) { error.value = (cause as Error).message } finally { loading.value = false } }
async function load() {
  if (busy.value) return
  const current = ++revision
  error.value = ''
  if (!episodeId.value) { timeline.value = null; releaseVideo(); return }
  try { const value = await api.get<Timeline>(`${root.value}/episodes/${episodeId.value}/timeline`); if (current !== revision || disposed) return; timeline.value = value; await loadVideo() }
  catch (cause) { if (current !== revision) return; if (cause instanceof ApiError && cause.code === 'RESOURCE_NOT_FOUND') { timeline.value = null; releaseVideo() } else error.value = (cause as Error).message }
}
async function changeEpisode() { timeline.value = null; releaseVideo(); notice.value = ''; loading.value = true; try { await load() } finally { loading.value = false } }
async function loadVideo() {
  const assetId = timeline.value?.final_asset_id || ''
  if (assetId === loadedAssetId) return
  releaseVideo()
  if (!assetId) return
  const value = await api.blob(`${root.value}/assets/${assetId}/content`)
  if (disposed || timeline.value?.final_asset_id !== assetId) { URL.revokeObjectURL(value); return }
  releaseVideo(); videoUrl.value = value; loadedAssetId = assetId
}
async function create() { if (busy.value) return; busy.value = true; revision++; error.value = ''; notice.value = ''; try { timeline.value = await api.post(`${root.value}/episodes/${episodeId.value}/timeline`); notice.value = '时间线已创建。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
async function sync() { if (!timeline.value || busy.value) return; busy.value = true; revision++; error.value = ''; notice.value = ''; try { timeline.value = await api.post(`${root.value}/timelines/${timeline.value.id}/sync`, { expected_version: timeline.value.version }); await loadVideo(); notice.value = '已同步批准镜头。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
async function render() { if (!timeline.value || busy.value) return; busy.value = true; revision++; error.value = ''; notice.value = ''; try { timeline.value = await api.post(`${root.value}/timelines/${timeline.value.id}/render`, { expected_version: timeline.value.version }); await loadVideo(); notice.value = '成片已渲染完成。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
watch(() => props.eventRevision, load); onMounted(loadEpisodes); onBeforeUnmount(() => { disposed = true; revision++; releaseVideo() })
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Timeline & Render</div><h1>成片时间线</h1><p>审核通过的素材沿视频、配音、音乐、音效和字幕轨道组成最终渲染计划。</p></div><div class="toolbar"><select v-model="episodeId" :disabled="busy" style="width:190px" aria-label="选择剧集" @change="changeEpisode"><option v-for="episode in episodes" :key="episode.id" :value="episode.id">EP {{ episode.episode_no }} · {{ episode.title }}</option></select><button class="btn" @click="load"><RefreshCcw :size="15" />刷新</button></div></div>
  <div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>
  <div v-if="loading" class="loading-state" role="status">正在读取时间线…</div><div v-else-if="!timeline" class="empty"><div><Film :size="31" color="#a5e7e8" /><strong style="margin-top:12px">尚未建立时间线</strong><p>为剧集创建时间线，然后同步已经审核的镜头。</p><button v-if="canEdit && episodeId" class="btn btn-primary" :disabled="busy" @click="create">创建时间线</button></div></div>
  <template v-else><section class="panel panel-pad"><div class="between"><div><h2>剧集轨道</h2><div class="row"><StatusPill :status="timeline.status" /><small>版本 {{ timeline.version }} · {{ duration.toFixed(1) }} 秒</small></div></div><div class="toolbar"><button v-if="canEdit" class="btn" :disabled="busy" @click="sync">同步已审镜头</button><button v-if="canEdit && timeline.status === 'READY'" class="btn btn-primary" :disabled="busy" @click="render"><Play :size="15" />{{ busy ? '正在渲染…' : '渲染成片' }}</button></div></div><div class="divider"></div><div class="timeline-editor"><div v-for="track in timeline.tracks" :key="track.id" class="track-row"><div class="track-name">{{ track.kind }}</div><div class="track-lane"><div v-for="item in timeline.items.filter(i => i.track_id === track.id)" :key="item.id" class="track-item" :style="{width:`${Math.max(50,item.duration_seconds*30)}px`}" :title="item.text || item.shot_id || ''">{{ item.text || `Shot ${item.shot_id?.slice(0,5)}` }}</div></div></div></div></section><section v-if="videoUrl" class="panel panel-pad" style="margin-top:18px"><div class="between"><h2>最终成片</h2><small>MP4 · {{ duration.toFixed(1) }} 秒</small></div><div class="video-frame"><video :src="videoUrl" controls preload="metadata"></video></div><div class="row" style="margin-top:16px"><RouterLink :to="`/projects/${project.id}/audit`" class="btn">查看生成溯源</RouterLink></div></section></template>
</template>
