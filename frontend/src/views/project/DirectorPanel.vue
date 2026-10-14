<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowRight, Check, Play, Send, Sparkles } from 'lucide-vue-next'
import { api } from '../../api'
import StatusPill from '../../components/StatusPill.vue'
import type { AgentRun, Character, Episode, Project, Scene, Shot, Story } from '../../types'
const props = defineProps<{ project: Project; canEdit: boolean; canReview: boolean; eventRevision: number }>()
const root = computed(() => `/projects/${props.project.id}`)
const stories = ref<Story[]>([]); const characters = ref<Character[]>([]); const episodes = ref<Episode[]>([]); const shots = ref<Shot[]>([]); const runs = ref<AgentRun[]>([]); const selected = ref<AgentRun | null>(null)
const request = ref('将故事制作成完整漫剧，自动完成配音、字幕和成片。'); const intent = ref('AUTOPRODUCE'); const shotId = ref(''); const storyTitle = ref(''); const storyContent = ref(''); const maxShots = ref(12); const busy = ref(false); const error = ref(''); const notice = ref('')
const videoUrl = ref(''); const loadingVideo = ref(false)
let pollTimer: ReturnType<typeof setInterval> | undefined
let refreshingRun = false
let disposed = false
async function playFinal(assetId: string) {
  loadingVideo.value = true
  try {
    const url = await api.blob(`${root.value}/assets/${assetId}/content`)
    if (disposed) { URL.revokeObjectURL(url); return }
    if (videoUrl.value) URL.revokeObjectURL(videoUrl.value)
    videoUrl.value = url
  } catch (cause) { error.value = (cause as Error).message }
  finally { loadingVideo.value = false }
}
watch(() => selected.value?.id, () => { if (videoUrl.value) URL.revokeObjectURL(videoUrl.value); videoUrl.value = '' })
const isAutoProduction = computed(() => intent.value === 'AUTOPRODUCE')
let loadRevision = 0
async function load() {
  const revision = ++loadRevision
  try {
    const [nextStories, nextCharacters, nextEpisodes, nextRuns] = await Promise.all([
      api.get<Story[]>(`${root.value}/stories`), api.get<Character[]>(`${root.value}/characters`),
      api.get<Episode[]>(`${root.value}/episodes`), api.get<AgentRun[]>(`${root.value}/director/runs`),
    ])
    const nextShots = (await Promise.all(nextEpisodes.map(async ep => {
      const scenes = await api.get<Scene[]>(`${root.value}/episodes/${ep.id}/scenes`)
      return (await Promise.all(scenes.map(scene => api.get<Shot[]>(`${root.value}/scenes/${scene.id}/shots`)))).flat()
    }))).flat()
    if (disposed || revision !== loadRevision) return
    stories.value = nextStories; characters.value = nextCharacters; episodes.value = nextEpisodes
    runs.value = nextRuns; shots.value = nextShots
    if (selected.value) selected.value = nextRuns.find(r => r.id === selected.value?.id) || selected.value
  } catch (cause) { if (!disposed && revision === loadRevision) error.value = (cause as Error).message }
}
async function start() { busy.value = true; error.value = ''; try { const payload: Record<string, unknown> = { request: request.value, intent: intent.value, shot_id: intent.value === 'ANALYZE' || isAutoProduction.value ? null : shotId.value }; if (isAutoProduction.value) { payload.story_title = storyTitle.value; payload.story_content = storyContent.value; payload.production = { max_shots: Number(maxShots.value), auto_generate_images: true } } const run = await api.post<AgentRun>(`${root.value}/director/runs`, payload); selected.value = run; runs.value.unshift(run); request.value = ''; notice.value = run.production ? `已创建 ${(run.production.created?.shots || 0)} 个镜头，图像任务已进入队列。` : run.status === 'WAITING_APPROVAL' ? 'Director 已提出操作，等待人工批准。' : 'Director 运行完成。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
async function approve(actionId: string) { try { await api.post(`${root.value}/pending-actions/${actionId}/approve`); await refreshRun(); notice.value = '操作已批准，可继续运行。' } catch (cause) { error.value = (cause as Error).message } }
async function resume() { if (!selected.value) return; busy.value = true; try { selected.value = await api.post(`/director/runs/${selected.value.id}/resume`); await load(); notice.value = 'Director 已继续执行。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
async function refreshRun() {
  if (!selected.value || refreshingRun) return
  const id = selected.value.id
  refreshingRun = true
  try {
    const updated = await api.get<AgentRun>(`/director/runs/${id}`)
    if (!disposed && selected.value?.id === id) selected.value = updated
    runs.value = runs.value.map(run => run.id === id ? updated : run)
  } catch (cause) { error.value = (cause as Error).message }
  finally { refreshingRun = false }
}
watch(() => props.eventRevision, refreshRun)
onMounted(async () => {
  try { await load(); if (!selected.value) selected.value = runs.value[0] || null }
  catch (cause) { error.value = (cause as Error).message }
  if (!disposed) pollTimer = setInterval(() => { if (['RUNNING','PLANNING'].includes(selected.value?.status || '')) void refreshRun() }, 4000)
})
onUnmounted(() => { disposed = true; clearInterval(pollTimer); if (videoUrl.value) URL.revokeObjectURL(videoUrl.value) })
</script>
<template>
  <section v-if="selected?.production" class="panel panel-pad" style="margin-bottom:16px" aria-live="polite">
    <h2>{{ selected.production.stage === 'COMPLETED' ? '漫剧已完成' : '自动制作' }}</h2>
    <p>角色、分镜、画面、配音、质量检查与视频合成会在后台自动执行，可关闭页面后回来查看。</p>
    <p v-if="selected.production.progress">镜头就绪：{{ selected.production.progress.ready_shots }} / {{ selected.production.progress.total_shots }}</p>
    <div class="row" v-if="selected.production.finals?.length">
      <button v-for="(final, index) in selected.production.finals" :key="final.asset_id" class="btn btn-primary" :disabled="loadingVideo" @click="playFinal(final.asset_id)">播放第 {{ index + 1 }} 集</button>
    </div>
    <template v-if="videoUrl"><video :src="videoUrl" controls style="width:100%;max-height:540px;border-radius:14px;margin-top:16px" /><a class="btn" :href="videoUrl" download="漫剧.mp4">下载成片</a></template>
  </section>
  <div class="section-head"><div><div class="section-kicker">Director Studio</div><h1>Director Studio</h1><p>Director 读取结构化项目数据与限定范围的知识，再通过受控工具请求生产动作。</p></div><div class="toolbar"><span class="status">{{ runs.length }} 次运行</span><button class="btn" @click="load">刷新</button></div></div>
  <div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>
  <div style="display:grid;grid-template-columns:220px minmax(0,1fr) 300px;gap:16px" class="director-layout"><aside class="panel panel-pad"><h3>项目上下文</h3><div class="run-node between"><span>原作</span><strong>{{ stories.length }}</strong></div><div class="run-node between"><span>角色</span><strong>{{ characters.length }}</strong></div><div class="run-node between"><span>剧集</span><strong>{{ episodes.length }}</strong></div><div class="run-node between"><span>镜头</span><strong>{{ shots.length }}</strong></div><div class="divider"></div><div class="mini-label">知识边界</div><p class="muted" style="font-size:12px;line-height:1.6;margin-top:8px">检索只返回当前项目的 ACTIVE 内容，工具调用会重新验证成员身份和资源状态。</p></aside>
  <section class="panel panel-pad"><h2>对话与计划</h2><div class="conversation"><template v-if="selected"><div class="message user">{{ selected.request }}</div><div class="message system"><strong>{{ selected.decision_summary || 'Director 正在处理…' }}</strong><div class="row" style="margin-top:9px"><StatusPill :status="selected.status" /></div></div><div v-if="selected.production" class="message system"><strong>自动制作进度</strong><div class="run-node">阶段：{{ selected.production.stage }}</div><div class="run-node">角色 {{ (selected.production.created?.characters || 0) }} · 剧集 {{ (selected.production.created?.episodes || 0) }} · 场景 {{ (selected.production.created?.scenes || 0) }} · 镜头 {{ (selected.production.created?.shots || 0) }}</div></div><div v-if="selected.warnings.length" class="message system">{{ selected.warnings.join(' · ') }}</div></template><div v-else class="empty" style="border:0"><div><Sparkles :size="28" color="#a5e7e8" /><strong style="margin-top:10px">准备制作下一步</strong><p>提交分析请求，或启动完整自动制作。</p></div></div></div><form style="margin-top:16px" @submit.prevent="start"><div class="field"><label>制作请求</label><textarea v-model="request" required rows="3" :placeholder="isAutoProduction ? '例如：把这段故事制作成第一集漫剧，保持角色和场景连续。' : '例如：分析项目上下文，并为选定镜头生成图像'"></textarea></div><template v-if="isAutoProduction"><div class="grid-two"><div class="field"><label>原作标题</label><input v-model="storyTitle" required maxlength="200" placeholder="第一集原作" /></div><div class="field"><label>最大镜头数</label><input v-model.number="maxShots" type="number" min="1" max="60" required /></div></div><div class="field"><label>故事正文</label><textarea v-model="storyContent" required minlength="20" rows="9" placeholder="粘贴小说或章节正文"></textarea></div></template><div class="row"><select v-model="intent" aria-label="执行意图"><option value="ANALYZE">分析上下文</option><option value="AUTOPRODUCE">完整自动制作</option><option value="GENERATE_IMAGE">生成图像</option><option value="GENERATE_VIDEO">生成视频</option><option value="GENERATE_VOICE">生成配音</option></select><select v-if="intent !== 'ANALYZE' && !isAutoProduction" v-model="shotId" required aria-label="目标镜头"><option value="" disabled>选择镜头</option><option v-for="shot in shots" :key="shot.id" :value="shot.id">Shot {{ shot.shot_no }} · {{ shot.description.slice(0,30) }}</option></select><button class="btn btn-primary" :disabled="busy || !canEdit || (isAutoProduction && (!storyTitle.trim() || storyContent.trim().length < 20)) || (!isAutoProduction && intent !== 'ANALYZE' && !shotId)"><Send :size="15" />{{ isAutoProduction ? '开始自动制作' : '运行' }}</button></div></form><div v-if="runs.length" class="divider"></div><div v-if="runs.length" class="list"><button v-for="run in runs.slice(0,5)" :key="run.id" class="list-row" style="color:inherit;text-align:left" @click="selected = run"><div><strong>{{ run.request.slice(0,52) }}</strong><br><small>{{ run.current_node }}</small></div><StatusPill :status="run.status" /></button></div></section>
  <aside class="panel panel-pad"><div class="between"><h3>Run Inspector</h3><button v-if="selected" class="btn btn-sm" @click="refreshRun">刷新</button></div><template v-if="selected"><div class="run-node between"><span>状态</span><StatusPill :status="selected.status" /></div><div class="run-node between"><span>当前节点</span><strong>{{ selected.current_node }}</strong></div><div class="run-node between"><span>检索来源</span><strong>{{ selected.retrieved_sources.length }}</strong></div><div class="run-node between"><span>工具调用</span><strong>{{ selected.tool_calls.length }}</strong></div><div class="run-node between"><span>生成任务</span><strong>{{ selected.generation_jobs.length }}</strong></div><div class="run-node between"><span>预计成本</span><strong>{{ selected.cost.toFixed(2) }}</strong></div><div class="divider"></div><div class="mini-label">待审批操作</div><div v-if="selected.pending_actions.length" class="list" style="margin-top:10px"><div v-for="action in selected.pending_actions" :key="action.id" class="subtle-panel" style="padding:12px"><div class="between"><strong style="font-size:12px">{{ action.action }}</strong><StatusPill :status="action.status" /></div><small class="mono">{{ action.target_id.slice(0,8) }}</small><button v-if="action.status === 'PENDING' && canReview" class="btn btn-sm btn-primary" style="width:100%;margin-top:10px" @click="approve(action.id)"><Check :size="13" />批准</button></div></div><p v-else class="muted" style="font-size:12px">暂无待审批操作。</p><button v-if="selected.status === 'WAITING_APPROVAL' && selected.pending_actions.some(a => a.status === 'APPROVED')" class="btn btn-primary" style="width:100%;margin-top:12px" @click="resume"><Play :size="14" />继续运行</button><div class="divider"></div><div class="mini-label">Tool Calls</div><div v-for="call in selected.tool_calls" :key="call.id" class="run-node">{{ call.name }} <span class="muted">· {{ call.status }}</span></div><div class="mini-label" style="margin-top:20px">Trace ID</div><p class="mono muted" style="word-break:break-all">{{ selected.trace_id }}</p></template><div v-else class="empty" style="min-height:320px"><div><strong>选择一次运行</strong><p>查看节点、来源、工具与审批记录。</p></div></div></aside></div>
</template>
<style scoped>@media(max-width:1350px){.director-layout{grid-template-columns:minmax(0,1fr) 290px!important}.director-layout>aside:first-child{display:none}}@media(max-width:850px){.director-layout{display:flex!important;flex-direction:column}}</style>
