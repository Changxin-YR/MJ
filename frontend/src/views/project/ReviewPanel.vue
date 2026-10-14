<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, LockKeyhole, RefreshCcw, X } from 'lucide-vue-next'
import { api } from '../../api'
import { videoPassed, videoReviewed } from '../../review'
import StatusPill from '../../components/StatusPill.vue'
import type { Episode, Project, Scene, Shot } from '../../types'
const props = defineProps<{ project: Project; canReview: boolean; canEdit: boolean; eventRevision: number }>()
const shots = ref<Shot[]>([]); const selected = ref<Shot | null>(null); const imageUrl = ref(''); const error = ref(''); const notice = ref(''); const reviewReason = ref('')
const pending = computed(() => shots.value.filter(s => ['REVIEW_REQUIRED','APPROVED'].includes(s.status) || (s.status === 'LOCKED' && !videoReviewed(s))))
const busy = ref(false)
const previewReady = ref(false)
let loadRevision = 0; let mediaRevision = 0; let disposed = false
function clearPreview() { previewReady.value = false; if (imageUrl.value) URL.revokeObjectURL(imageUrl.value); imageUrl.value = '' }
async function load() {
  const revision = ++loadRevision
  try {
    const root = `/projects/${props.project.id}`
    const episodes = await api.get<Episode[]>(`${root}/episodes`)
    const all = (await Promise.all(episodes.map(async episode => {
      const scenes = await api.get<Scene[]>(`${root}/episodes/${episode.id}/scenes`)
      return (await Promise.all(scenes.map(scene => api.get<Shot[]>(`${root}/scenes/${scene.id}/shots`)))).flat()
    }))).flat()
    if (disposed || revision !== loadRevision) return
    shots.value = all; error.value = ''
    if (selected.value) {
      const updated = all.find(s => s.id === selected.value?.id)
      if (!updated) { selected.value = null; mediaRevision++; clearPreview() }
      else if ((updated.current_video_asset_id || updated.current_image_asset_id) !== (selected.value.current_video_asset_id || selected.value.current_image_asset_id)) await selectShot(updated)
      else selected.value = updated
    }
  } catch (cause) { if (!disposed && revision === loadRevision) error.value = (cause as Error).message }
}
async function selectShot(shot: Shot) {
  const revision = ++mediaRevision
  selected.value = shot; reviewReason.value = ''; error.value = ''; notice.value = ''; clearPreview()
  const assetId = shot.current_video_asset_id || shot.current_image_asset_id
  if (!assetId) return
  try {
    const url = await api.blob(`/projects/${props.project.id}/assets/${assetId}/content`)
    if (disposed || revision !== mediaRevision) URL.revokeObjectURL(url)
    else { imageUrl.value = url; previewReady.value = true }
  } catch (cause) { if (!disposed && revision === mediaRevision) error.value = (cause as Error).message }
}
onBeforeUnmount(() => { disposed = true; mediaRevision++; loadRevision++; clearPreview() })
async function transition(target: string) { if (!selected.value || busy.value || (target !== 'REJECTED' && !previewReady.value)) return; busy.value = true; error.value = ''; notice.value = ''; try { await api.post(`/projects/${props.project.id}/shots/${selected.value.id}/transition`, { expected_version: selected.value.version, target, review_reason: target === 'APPROVED' ? reviewReason.value : undefined }); notice.value = `审核结果：${target}`; reviewReason.value = ''; await load() } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
watch(() => props.eventRevision, load); onMounted(load)
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Human Review</div><h1>镜头审核</h1><p>生成输出需经人工确认，正式批准后才可进入成片时间线。</p></div><button class="btn" @click="load"><RefreshCcw :size="15" />刷新</button></div><div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>
  <div class="split"><section class="panel panel-pad"><div class="between"><h2>待处理镜头</h2><span class="status">{{ pending.length }}</span></div><div v-if="pending.length" class="list"><button v-for="shot in pending" :key="shot.id" class="list-row" style="text-align:left;color:inherit" @click="selectShot(shot)"><div><strong>Shot {{ String(shot.shot_no).padStart(2,'0') }} · {{ shot.description.slice(0,45) }}</strong><br><small>{{ shot.duration }} 秒 · {{ shot.dialogue || '无对白' }}</small></div><StatusPill :status="shot.status" /></button></div><div v-else class="empty"><div><strong>暂无待审镜头</strong><p>完成生成后，镜头会进入人工审核。</p></div></div></section><aside class="panel panel-pad"><h2>{{ selected ? `Shot ${selected.shot_no}` : '选择镜头' }}</h2><template v-if="selected"><div class="shot-thumb" style="height:auto;aspect-ratio:16/9;border-radius:12px"><video v-if="imageUrl && selected.current_video_asset_id" :src="imageUrl" controls aria-label="待审镜头视频" /><img v-else-if="imageUrl" :src="imageUrl" alt="待审镜头图像" /></div><p class="story-text" style="margin-top:15px">{{ selected.description }}</p><p class="muted" style="font-size:12px">对白：{{ selected.dialogue || '无' }}</p><div class="divider"></div><div class="mini-label">素材核对</div><div class="run-node">图像：{{ selected.current_image_asset_id ? '已生成' : '未生成' }}</div><div class="run-node">视频：{{ selected.current_video_asset_id ? '已生成' : '未生成' }}</div><div class="run-node">配音：{{ selected.current_audio_asset_id ? '已生成' : '未生成' }}</div><div class="divider"></div><div class="mini-label">Inspector</div><template v-if="selected.inspection_json"><div class="run-node">{{ selected.inspection_json.status }} · {{ selected.inspection_json.method }} · 分数 {{ (selected.inspection_json.score ?? 0).toFixed(2) }}</div><div v-for="(value, key) in selected.inspection_json.checks" :key="key" class="run-node">{{ key }}：{{ value }}</div><p v-for="issue in selected.inspection_json.issues" :key="issue" class="muted" style="font-size:12px">{{ issue }}</p></template><div v-else class="run-node">等待生成与检查</div><div v-if="canReview && ['REVIEW_REQUIRED', 'APPROVED', 'LOCKED'].includes(selected.status) && selected.current_video_asset_id && (selected.status === 'REVIEW_REQUIRED' ? !videoPassed(selected) : !videoReviewed(selected))" class="field" style="margin-top:16px"><label for="review-reason">人工复核通过理由（检查失败或未验证）</label><textarea id="review-reason" v-model="reviewReason" maxlength="500" rows="2" placeholder="说明人工复核依据；也可直接驳回并重生成"></textarea></div><div v-if="canReview" class="toolbar" style="margin-top:20px"><button v-if="selected.status === 'REVIEW_REQUIRED'" class="btn btn-primary" :disabled="busy || !previewReady || !selected.current_video_asset_id || (!videoPassed(selected) && !reviewReason.trim())" @click="transition('APPROVED')"><Check :size="15" />通过</button><button v-if="selected.status === 'REVIEW_REQUIRED'" class="btn btn-danger" @click="transition('REJECTED')"><X :size="15" />驳回</button><button v-if="['APPROVED', 'LOCKED'].includes(selected.status) && selected.current_video_asset_id && !videoReviewed(selected)" class="btn btn-primary" :disabled="busy || !previewReady || !reviewReason.trim()" @click="transition('APPROVED')"><Check :size="15" />确认复检</button><button v-if="selected.status === 'APPROVED' && videoReviewed(selected)" class="btn" :disabled="busy || !previewReady" @click="transition('LOCKED')"><LockKeyhole :size="15" />锁定</button></div></template><div v-else class="empty"><div><strong>等待选择</strong><p>查看素材、对白与状态后给出审核结果。</p></div></div></aside></div>
</template>
