<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Plus, UserRound } from 'lucide-vue-next'
import { api } from '../../api'
import StatusPill from '../../components/StatusPill.vue'
import type { Character, Project } from '../../types'
const props = defineProps<{ project: Project; canEdit: boolean; canReview: boolean }>()
const characters = ref<Character[]>([]); const selected = ref<Character | null>(null); const name = ref(''); const background = ref(''); const face = ref(''); const hair = ref(''); const costume = ref(''); const style = ref(''); const voice = ref(''); const anchor = ref(''); const negativePrompt = ref(''); const error = ref(''); const notice = ref(''); const busy = ref(false)
const root = computed(() => `/projects/${props.project.id}/characters`)
async function load() { characters.value = await api.get(root.value); if (selected.value) await select(selected.value.id) }
async function select(id: string) {
  const character = await api.get<Character>(`${root.value}/${id}`)
  selected.value = character
  const base = character.versions?.find(version => version.id === character.active_version_id) || character.versions?.[0]
  background.value = base?.background || ''
  const source = base?.dna || {}
  face.value = String(source.face || '')
  hair.value = String(source.hair || '')
  costume.value = String(source.costume || '')
  style.value = String(source.style || '')
  voice.value = String(source.voice || '')
  anchor.value = String(source.prompt_anchor || '')
  negativePrompt.value = String(source.negative_prompt || '')
}
function clearForm() { background.value = ''; face.value = ''; hair.value = ''; costume.value = ''; style.value = ''; voice.value = ''; anchor.value = ''; negativePrompt.value = '' }
function dna() { return { face: face.value, hair: hair.value, costume: costume.value, style: style.value, voice: voice.value, prompt_anchor: anchor.value, negative_prompt: negativePrompt.value } }
async function create() { busy.value = true; error.value = ''; try { const result = await api.post<Character>(root.value, { name: name.value, background: background.value, dna: dna() }); await load(); await select(result.id); name.value = ''; notice.value = '角色草稿已创建。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
async function draft() { if (!selected.value) return; busy.value = true; try { await api.post(`${root.value}/${selected.value.id}/versions`, { expected_version: selected.value.version, background: background.value, dna: dna() }); await load(); notice.value = '新版本草稿已创建。' } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false } }
async function activate(versionId: string) { if (!selected.value) return; try { await api.post(`${root.value}/${selected.value.id}/versions/${versionId}/activate`, { expected_version: selected.value.version }); await load(); notice.value = '版本已设为正式。' } catch (cause) { error.value = (cause as Error).message } }
onMounted(load)
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Character Studio</div><h1>角色工作室</h1><p>维护角色 DNA；正式版本由人工审核激活，生成提示词引用其锚点。</p></div></div>
  <div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>
  <div class="split"><div class="stack"><section class="panel panel-pad"><h2>角色</h2><div v-if="characters.length" class="list"><button v-for="character in characters" :key="character.id" class="list-row" style="text-align:left;color:inherit" @click="select(character.id)"><div class="row"><div class="character-avatar">{{ character.name.slice(0,1) }}</div><div><strong>{{ character.name }}</strong><br><small>{{ character.active_version_id ? '有正式版本' : '等待审核' }}</small></div></div><UserRound :size="15" /></button></div><div v-else class="empty"><div><strong>还没有角色</strong><p>先创建角色草稿，再激活正式版本。</p></div></div></section><section v-if="selected" class="panel panel-pad"><div class="between"><h2>{{ selected.name }}的版本</h2><span class="mini-label">版本锁 {{ selected.version }}</span></div><div class="list"><div v-for="version in selected.versions" :key="version.id" class="list-row"><div><strong>版本 {{ version.version_no }}</strong><br><small>{{ version.background || '暂无背景描述' }}</small></div><div class="row"><StatusPill :status="version.status" /><button v-if="version.status === 'DRAFT' && canReview" class="btn btn-sm btn-primary" @click="activate(version.id)">激活</button></div></div></div></section></div>
  <section v-if="canEdit" class="panel panel-pad"><h2>{{ selected ? `为 ${selected.name} 创建草稿` : '新建角色' }}</h2><form @submit.prevent="selected ? draft() : create()"><div v-if="!selected" class="field"><label>角色名称</label><input v-model="name" required placeholder="角色姓名" /></div><div class="field"><label>背景</label><textarea v-model="background" rows="3" placeholder="人物经历、动机与性格"></textarea></div><div class="grid-two"><div class="field"><label>面部</label><input v-model="face" placeholder="五官与辨识特征" /></div><div class="field"><label>发型</label><input v-model="hair" placeholder="颜色、长度、造型" /></div><div class="field"><label>服装</label><input v-model="costume" placeholder="常用服装" /></div><div class="field"><label>风格</label><input v-model="style" placeholder="画面风格" /></div></div><div class="field"><label>声音</label><input v-model="voice" placeholder="音色与语气" /></div><div class="field"><label>Prompt Anchor</label><textarea v-model="anchor" rows="3" placeholder="生成时必须保持的核心视觉描述"></textarea></div><div class="field"><label>Negative Prompt</label><textarea v-model="negativePrompt" rows="2" placeholder="例如：金色长发、现代服装、错误文字"></textarea></div><div class="row"><button class="btn btn-primary" :disabled="busy"><Plus :size="15" />{{ selected ? '创建新草稿' : '创建角色' }}</button><button v-if="selected" type="button" class="btn btn-ghost" @click="selected = null; clearForm()">新建其他角色</button></div></form></section></div>
</template>
