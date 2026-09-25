<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Film, Play, RefreshCcw } from 'lucide-vue-next'
import { api, ApiError } from '../../api'
import StatusPill from '../../components/StatusPill.vue'
import type { Asset, Episode, Project, Timeline } from '../../types'
const props = defineProps<{ project: Project; canEdit: boolean; eventRevision: number }>()
const root = computed(() => `/projects/${props.project.id}`)
const episodes = ref<Episode[]>([]); const episodeId = ref(''); const timeline = ref<Timeline | null>(null); const videoUrl = ref(''); const busy = ref(false); const error = ref(''); const notice = ref(''); const audioFile = ref<File | null>(null); const audioKind = ref('MUSIC'); const audioStart = ref(0)
const duration = computed(() => {
  const videoTrack = timeline.value?.tracks.find(track => track.kind === 'VIDEO')
  if (!videoTrack) return 0
  return Math.max(0, ...(timeline.value?.items || []).filter(item => item.track_id === videoTrack.id).map(item => item.start_seconds + item.duration_seconds))
})
async function loadEpisodes() { episodes.value = await api.get(`${root.value}/episodes`); if (!episodeId.value && episodes.value.length) episodeId.value = episodes.value[0].id; await load() }
async function load() { if (!episodeId.value) return; try { timeline.value = await api.get(`${root.value}/episodes/${episodeId.value}/timeline`); await loadVideo() } catch (cause) { if (cause instanceof ApiError && cause.code === 'RESOURCE_NOT_FOUND') timeline.value = null; else error.value = (cause as Error).message } }
async function loadVideo() { if (videoUrl.value) URL.revokeObjectURL(videoUrl.value); videoUrl.value = timeline.value?.final_asset_id ? await api.blob(`${root.value}/assets/${timeline.value.final_asset_id}/content`) : '' }
async function create() { try { timeline.value = await api.post(`${root.value}/episodes/${episodeId.value}/timeline`); notice.value = '时间线已创建。' } catch (cause) { error.value = (cause as Error).message } }
async function sync() { if (!timeline.value) return; try { timeline.value = await api.post(`${root.value}/timelines/${timeline.value.id}/sync`, { expected_version: timeline.value.version }); notice.value = '已同步批准镜头。' } catch (cause) { error.value = (cause as Error).message } }
async function render() { if (!timeline.value) return; busy.value = true; error.value = ''; try { timeline.value = await api.post(`${root.value}/timelines/${timeline.value.id}/render`, { expected_version: timeline.value.version }); await loadVideo(); notice.value = '成片已渲染完成。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
function chooseAudio(event: Event) { audioFile.value = (event.target as HTMLInputElement).files?.[0] || null }
async function addAudio() {
  if (!timeline.value || !audioFile.value) return
  busy.value = true
  error.value = ''
  try {
    const asset = await api.upload<Asset>(`${root.value}/assets/uploads/direct`, audioFile.value)
    timeline.value = await api.post(`${root.value}/timelines/${timeline.value.id}/audio-items`, {
      expected_version: timeline.value.version,
      kind: audioKind.value,
      asset_id: asset.id,
      start_seconds: audioStart.value,
    })
    audioFile.value = null
    audioStart.value = 0
    await loadVideo()
    notice.value = `${audioKind.value} 已加入时间线。`
  } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false }
}
async function removeAudio(itemId: string) {
  if (!timeline.value) return
  try {
    timeline.value = await api.post(`${root.value}/timelines/${timeline.value.id}/items/${itemId}/remove`, { expected_version: timeline.value.version })
    await loadVideo()
    notice.value = '音频轨道项已移除。'
  } catch (cause) { error.value = (cause as Error).message }
}
watch(() => props.eventRevision, load); onMounted(loadEpisodes); onBeforeUnmount(() => { if (videoUrl.value) URL.revokeObjectURL(videoUrl.value) })
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Timeline & Render</div><h1>成片时间线</h1><p>审核通过的素材沿视频、配音、音乐、音效和字幕轨道组成最终渲染计划。</p></div><div class="toolbar"><select v-model="episodeId" style="width:190px" aria-label="选择剧集" @change="load"><option v-for="episode in episodes" :key="episode.id" :value="episode.id">EP {{ episode.episode_no }} · {{ episode.title }}</option></select><button class="btn" @click="load"><RefreshCcw :size="15" />刷新</button></div></div>
  <div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>
  <div v-if="!timeline" class="empty"><div><Film :size="31" color="#a5e7e8" /><strong style="margin-top:12px">尚未建立时间线</strong><p>为剧集创建时间线，然后同步已经审核的镜头。</p><button v-if="canEdit && episodeId" class="btn btn-primary" @click="create">创建时间线</button></div></div>
  <template v-else><section class="panel panel-pad"><div class="between"><div><h2>剧集轨道</h2><div class="row"><StatusPill :status="timeline.status" /><small>版本 {{ timeline.version }} · {{ duration.toFixed(1) }} 秒</small></div></div><div class="toolbar"><button v-if="canEdit" class="btn" @click="sync">同步已审镜头</button><button v-if="canEdit && timeline.status === 'READY'" class="btn btn-primary" :disabled="busy" @click="render"><Play :size="15" />{{ busy ? '正在渲染…' : '渲染成片' }}</button></div></div><div class="divider"></div><div class="timeline-editor"><div v-for="track in timeline.tracks" :key="track.id" class="track-row"><div class="track-name">{{ track.kind }}</div><div class="track-lane"><div v-for="item in timeline.items.filter(i => i.track_id === track.id)" :key="item.id" class="track-item" :style="{width:`${Math.max(50,item.duration_seconds*30)}px`}" :title="item.text || item.shot_id || ''"><span>{{ item.text || `Shot ${item.shot_id?.slice(0,5)}` }}</span><button v-if="canEdit && ['MUSIC','SFX'].includes(track.kind)" class="icon-button" title="移除音频" @click="removeAudio(item.id)">×</button></div></div></div></div></section><section v-if="canEdit" class="panel panel-pad" style="margin-top:18px"><h2>音乐与音效</h2><p class="muted" style="font-size:12px">上传 WAV 后加入 MUSIC 或 SFX 轨；重新同步镜头不会删除这些手工音频。</p><div class="row" style="align-items:end;flex-wrap:wrap"><div class="field" style="min-width:150px"><label>轨道</label><select v-model="audioKind" aria-label="音频轨道"><option value="MUSIC">MUSIC</option><option value="SFX">SFX</option></select></div><div class="field" style="min-width:150px"><label>开始时间（秒）</label><input v-model.number="audioStart" aria-label="音频开始时间" type="number" min="0" step="0.1" /></div><div class="field" style="min-width:240px"><label>WAV 文件</label><input aria-label="选择 WAV 文件" type="file" accept=".wav,audio/wav" @change="chooseAudio" /></div><button class="btn" :disabled="busy || !audioFile" @click="addAudio">加入音频轨</button></div></section><section v-if="videoUrl" class="panel panel-pad" style="margin-top:18px"><div class="between"><h2>最终成片</h2><small>MP4 · {{ duration.toFixed(1) }} 秒</small></div><div class="video-frame"><video :src="videoUrl" controls preload="metadata"></video></div><div class="row" style="margin-top:16px"><RouterLink :to="`/projects/${project.id}/audit`" class="btn">查看生成溯源</RouterLink></div></section></template>
</template>
