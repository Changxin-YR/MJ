<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { ArrowDown, ArrowUp, Image, LockKeyhole, Plus, RefreshCcw, Save, Sparkles } from 'lucide-vue-next'
import { api } from '../../api'
import { videoPassed, videoReviewed } from '../../review'
import StatusPill from '../../components/StatusPill.vue'
import type { Episode, Project, Scene, Shot } from '../../types'
defineOptions({ inheritAttrs: false })
const props = defineProps<{ project: Project; canEdit: boolean; canReview: boolean; eventRevision: number }>()
const episodes = ref<Episode[]>([]); const episodeId = ref(''); const scenes = ref<Scene[]>([]); const sceneId = ref(''); const shots = ref<Shot[]>([]); const selected = ref<Shot | null>(null)
type Draft = Pick<Shot, 'description' | 'action' | 'dialogue' | 'emotion' | 'shot_type' | 'camera_angle' | 'camera_movement' | 'duration' | 'prompt' | 'negative_prompt' | 'character_ids'>
function emptyDraft(): Draft { return { description: '', action: '', dialogue: '', emotion: '', shot_type: 'medium', camera_angle: 'eye-level', camera_movement: 'static', duration: 6, prompt: '', negative_prompt: '', character_ids: [] } }
const editing = ref(false); const draft = ref<Draft>(emptyDraft()); const savedDraft = ref(JSON.stringify(draft.value)); const editVersion = ref(0)
const dirty = computed(() => JSON.stringify(draft.value) !== savedDraft.value)
const canChange = computed(() => props.canEdit && !['LOCKED','APPROVED','GENERATING','GENERATED','INSPECTING','REVIEW_REQUIRED'].includes(selected.value?.status || ''))
const thumbs = ref<Record<string,string>>({}); const builtPrompt = ref(''); const error = ref(''); const notice = ref(''); const busy = ref(false)
const root = computed(() => `/projects/${props.project.id}`)
const total = computed(() => shots.value.reduce((sum, shot) => sum + shot.duration, 0))
let listRevision = 0; let disposed = false
async function loadEpisodes() { try { episodes.value = await api.get<Episode[]>(`${root.value}/episodes`); if (!episodeId.value && episodes.value.length) episodeId.value = episodes.value[0]!.id; await loadScenes() } catch (cause) { error.value = (cause as Error).message } }
async function loadScenes() { try { scenes.value = episodeId.value ? await api.get(`${root.value}/episodes/${episodeId.value}/scenes`) : []; if (!scenes.value.some(s => s.id === sceneId.value)) sceneId.value = scenes.value[0]?.id || ''; await loadShots() } catch (cause) { error.value = (cause as Error).message } }
async function loadShots() {
  const current = ++listRevision
  try {
    const items = sceneId.value ? await api.get<Shot[]>(`${root.value}/scenes/${sceneId.value}/shots`) : []
    if (current !== listRevision || disposed) return
    shots.value = items
    if (selected.value) { selected.value = items.find(s => s.id === selected.value?.id) || null; if (selected.value && !dirty.value) fillDraft(selected.value) }
    for (const shot of items) {
      const assetId = shot.current_image_asset_id
      if (assetId && !thumbs.value[assetId]) {
        try { const url = await api.blob(`${root.value}/assets/${assetId}/content`); if (disposed || thumbs.value[assetId]) URL.revokeObjectURL(url); else thumbs.value[assetId] = url } catch { /* The full media view offers a retry. */ }
      }
    }
  } catch (cause) { error.value = (cause as Error).message }
}
function fillDraft(shot: Shot) { const values = emptyDraft(); for (const key of Object.keys(values) as (keyof Draft)[]) Object.assign(values, { [key]: shot[key] }); values.character_ids = [...shot.character_ids]; draft.value = values; savedDraft.value = JSON.stringify(values); editVersion.value = shot.version }
function allowLeave() { return !dirty.value || window.confirm('镜头有未保存的修改，确定离开并放弃这些修改吗？') }
function selectShot(shot: Shot) { if (!allowLeave()) return; selected.value = shot; editing.value = false; fillDraft(shot); builtPrompt.value = ''; error.value = ''; notice.value = '' }
function newShot() { if (!allowLeave()) return; selected.value = null; editing.value = true; draft.value = emptyDraft(); savedDraft.value = JSON.stringify(draft.value); error.value = ''; notice.value = '' }
function changeScene(id: string) { if (!allowLeave()) return; selected.value = null; editing.value = false; draft.value = emptyDraft(); savedDraft.value = JSON.stringify(draft.value); sceneId.value = id; void loadShots() }
function changeEpisode(event: Event) { const input = event.target as HTMLSelectElement; if (!allowLeave()) { input.value = episodeId.value; return }; episodeId.value = input.value; selected.value = null; editing.value = false; draft.value = emptyDraft(); savedDraft.value = JSON.stringify(draft.value); void loadScenes() }
async function save() {
  if (!sceneId.value || busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = selected.value ? await api.patch<Shot>(`${root.value}/shots/${selected.value.id}`, { ...draft.value, expected_version: editVersion.value }) : await api.post<Shot>(`${root.value}/scenes/${sceneId.value}/shots`, draft.value)
    notice.value = selected.value ? '镜头已保存。' : '镜头已创建。'; selected.value = result; fillDraft(result); editing.value = false; await loadShots()
  } catch (cause) { error.value = (cause as Error).message }
  finally { busy.value = false }
}
async function transition(target: string) {
  if (!selected.value || busy.value || dirty.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { selected.value = await api.post(`${root.value}/shots/${selected.value.id}/transition`, { expected_version: selected.value.version, target }); await loadShots(); notice.value = `镜头已进入${target}。` }
  catch (cause) { error.value = (cause as Error).message } finally { busy.value = false }
}
async function generate(kind: string) {
  if (!selected.value || busy.value || dirty.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await api.post(`${root.value}/shots/${selected.value.id}/generations`, { kind, idempotency_key: crypto.randomUUID() }); await loadShots(); notice.value = `${kind}任务已加入队列，可在生成队列中查看进度。` }
  catch (cause) { error.value = (cause as Error).message } finally { busy.value = false }
}
async function reorder(delta: number) { if (!selected.value || busy.value || dirty.value) return; busy.value = true; try { selected.value = await api.post(`${root.value}/shots/${selected.value.id}/reorder`, { expected_version: selected.value.version, target_no: selected.value.shot_no + delta }); await loadShots() } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
async function previewPrompt() { if (!selected.value) return; try { const result = await api.get<{prompt:string}>(`${root.value}/shots/${selected.value.id}/built-prompt`); builtPrompt.value = result.prompt } catch (cause) { error.value = (cause as Error).message } }
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
onBeforeRouteLeave(allowLeave)
onBeforeRouteUpdate(allowLeave)
watch(() => props.eventRevision, loadShots)
onMounted(() => { void loadEpisodes(); window.addEventListener('beforeunload', beforeUnload) })
onBeforeUnmount(() => { disposed = true; window.removeEventListener('beforeunload', beforeUnload); Object.values(thumbs.value).forEach(URL.revokeObjectURL) })
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Storyboard Studio</div><h1>分镜工作室</h1><p>场景、镜头画板、参数检查器与时间线预览集中在同一工作台。</p></div><div class="toolbar"><select :value="episodeId" style="width:200px" aria-label="选择剧集" @change="changeEpisode"><option v-for="episode in episodes" :key="episode.id" :value="episode.id">EP {{ episode.episode_no }} · {{ episode.title }}</option></select><button class="btn" @click="loadShots"><RefreshCcw :size="15" />刷新</button></div></div>
  <div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>
  <div style="display:grid;grid-template-columns:190px minmax(0,1fr) 310px;gap:15px" class="storyboard-layout">
    <section class="panel panel-pad" style="padding:15px"><div class="between"><h3 style="margin:0">场景</h3><span class="mini-label">{{ scenes.length }}</span></div><div class="list" style="margin-top:14px"><button v-for="scene in scenes" :key="scene.id" class="scene-row" :class="{active:sceneId === scene.id}" style="color:inherit;text-align:left" @click="changeScene(scene.id)"><span class="mini-label">{{ String(scene.scene_no).padStart(2,'0') }}</span><span style="font-size:12px">{{ scene.heading }}</span></button></div><div v-if="!scenes.length" class="empty" style="min-height:160px;padding:20px"><div><strong>暂无场景</strong><p>先在剧集页面添加场景。</p></div></div></section>
    <section class="panel panel-pad"><div class="between" style="margin-bottom:18px"><div><h2 style="margin-bottom:4px">镜头画板</h2><small>{{ shots.length }} 个镜头 · {{ total }} 秒</small></div><button v-if="canEdit && sceneId" class="btn btn-primary" @click="newShot"><Plus :size="15" />创建镜头</button></div><div v-if="shots.length" class="shot-grid"><button v-for="shot in shots" :key="shot.id" class="shot-card" :class="{active:selected?.id === shot.id}" style="text-align:left;color:inherit" @click="selectShot(shot)"><div class="shot-thumb"><img v-if="thumbs[shot.current_image_asset_id || '']" :src="thumbs[shot.current_image_asset_id || '']" alt="镜头生成图" /><Image v-else :size="28" /></div><div class="shot-info"><div class="between"><strong>Shot {{ String(shot.shot_no).padStart(2,'0') }}</strong><StatusPill :status="shot.status" /></div><p>{{ shot.description || '未填写描述' }}</p></div></button></div><div v-else class="empty"><div><strong>还没有镜头</strong><p>选择场景并创建第一个镜头。</p></div></div></section>
    <aside class="inspector"><div class="between"><h3>{{ selected ? `Shot ${String(selected.shot_no).padStart(2,'0')} 检查器` : '新镜头' }}</h3><StatusPill v-if="selected" :status="selected.status" /></div><template v-if="selected || editing"><div class="grid-two"><div class="field"><label>景别</label><input v-model="draft.shot_type" :disabled="!canChange" /></div><div class="field"><label for="shot-duration">时长（秒）</label><input id="shot-duration" v-model.number="draft.duration" type="number" min="1" max="30" :disabled="!canChange" /></div></div><div class="field"><label>机位角度</label><input v-model="draft.camera_angle" :disabled="!canChange" /></div><div class="field"><label>镜头运动</label><input v-model="draft.camera_movement" :disabled="!canChange" /></div><div class="field"><label for="shot-description">画面描述</label><textarea id="shot-description" v-model="draft.description" rows="3" :disabled="!canChange"></textarea></div><div class="field"><label>动作</label><textarea v-model="draft.action" rows="2" :disabled="!canChange"></textarea></div><div class="field"><label for="shot-dialogue">对白</label><textarea id="shot-dialogue" v-model="draft.dialogue" rows="2" :disabled="!canChange"></textarea></div><div class="field"><label>Prompt</label><textarea v-model="draft.prompt" rows="2" :disabled="!canChange"></textarea></div><button v-if="canChange" class="btn btn-primary" :disabled="busy" @click="save"><Save :size="15" />保存镜头</button><p v-if="dirty" class="notice">有未保存的修改，请先保存再生成。</p><div v-if="selected" class="divider"></div><div v-if="selected" class="toolbar"><button v-if="canEdit && selected.status === 'DRAFT'" class="btn btn-sm" @click="transition('PLANNED')">规划完成</button><button v-if="canEdit && selected.status === 'PLANNED'" class="btn btn-sm" @click="transition('STORYBOARD_READY')">分镜就绪</button><button v-if="canReview && selected.status === 'REVIEW_REQUIRED' && videoPassed(selected)" class="btn btn-sm btn-primary" @click="transition('APPROVED')">通过审核</button><button v-if="canReview && selected.status === 'APPROVED' && videoReviewed(selected)" class="btn btn-sm" @click="transition('LOCKED')"><LockKeyhole :size="13" />锁定</button><button v-if="canEdit && ['STORYBOARD_READY','REVIEW_REQUIRED'].includes(selected.status)" class="btn btn-sm" :disabled="busy || dirty" @click="generate('IMAGE')"><Sparkles :size="13" />生成图像</button><button v-if="canEdit && selected.current_image_asset_id && selected.status === 'REVIEW_REQUIRED'" class="btn btn-sm" :disabled="busy || dirty" @click="generate('VIDEO')">生成视频</button><button v-if="canEdit && selected.dialogue && selected.status === 'REVIEW_REQUIRED'" class="btn btn-sm" :disabled="busy || dirty" @click="generate('VOICE')">生成配音</button><button v-if="canEdit && selected.status === 'REJECTED'" class="btn btn-sm" :disabled="busy || dirty" @click="transition('PLANNED')">重新规划</button><button v-if="canEdit && selected.status === 'FAILED'" class="btn btn-sm" :disabled="busy || dirty" @click="transition('STORYBOARD_READY')">准备重试</button><button v-if="canReview && selected.status === 'APPROVED'" class="btn btn-sm" :disabled="busy || dirty" @click="transition('REJECTED')">退回修改</button><button class="btn btn-sm" @click="previewPrompt">预览提示词</button><button v-if="canChange && selected.shot_no > 1" class="icon-button" title="上移" @click="reorder(-1)"><ArrowUp :size="14" /></button><button v-if="canChange && selected.shot_no < shots.length" class="icon-button" title="下移" @click="reorder(1)"><ArrowDown :size="14" /></button></div><p v-if="builtPrompt" class="story-text subtle-panel" style="padding:12px;margin-top:12px">{{ builtPrompt }}</p></template><div v-else class="empty" style="min-height:300px"><div><strong>选择镜头</strong><p>查看参数、生成素材或推进审核。</p></div></div></aside>
  </div>
  <section class="panel panel-pad" style="margin-top:16px"><div class="between"><h3 style="margin:0">时间线预览</h3><small>{{ total }} 秒</small></div><div class="timeline-strip" style="margin-top:13px"><div v-for="shot in shots" :key="shot.id" class="timeline-clip" :style="{width:`${Math.max(100,shot.duration*20)}px`}"><strong>{{ String(shot.shot_no).padStart(2,'0') }}</strong><br>{{ shot.duration }}s · {{ shot.status }}</div><small v-if="!shots.length">镜头出现后将显示在这里。</small></div></section>
</template>
<style scoped>@media(max-width:1350px){.storyboard-layout{grid-template-columns:160px minmax(0,1fr)!important}.storyboard-layout .inspector{grid-column:1/-1}}@media(max-width:760px){.storyboard-layout{display:flex!important;flex-direction:column}}</style>
