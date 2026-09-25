<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BookOpen, Plus, Search, Trash2, Upload } from 'lucide-vue-next'
import { api } from '../../api'
import type { Project, ProjectBible, Story } from '../../types'

const props = defineProps<{ project: Project; canEdit: boolean }>()
const stories = ref<Story[]>([])
const bibles = ref<ProjectBible[]>([])
const selectedId = ref('')
const title = ref('')
const content = ref('')
const searchText = ref('')
const results = ref<{text:string; source_id:string; score:number}[]>([])
const bibleEditingId = ref('')
const bibleTitle = ref('')
const bibleContent = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')

const selected = computed(() => stories.value.find(s => s.id === selectedId.value))
const selectedBible = computed(() => bibles.value.find(item => item.id === bibleEditingId.value))
const root = computed(() => `/projects/${props.project.id}`)

async function load() {
  const [storyRows, bibleRows] = await Promise.all([
    api.get<Story[]>(`${root.value}/stories`),
    api.get<ProjectBible[]>(`${root.value}/bibles`),
  ])
  stories.value = storyRows
  bibles.value = bibleRows
  if (!stories.value.some(story => story.id === selectedId.value)) selectedId.value = stories.value[0]?.id || ''
  if (bibleEditingId.value && !bibles.value.some(item => item.id === bibleEditingId.value)) resetBible()
}
async function importStory() {
  busy.value = true; error.value = ''
  try {
    const story = await api.post<Story>(`${root.value}/stories`, { title: title.value, content: content.value })
    stories.value.unshift(story); selectedId.value = story.id; title.value = ''; content.value = ''; notice.value = '故事已导入。'
  } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false }
}
async function indexStory() {
  if (!selected.value) return
  busy.value = true; error.value = ''
  try {
    await api.post(`${root.value}/knowledge/stories/${selected.value.id}/index`)
    notice.value = '故事已进入项目知识索引。'
  } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false }
}
async function search() {
  try {
    results.value = await api.post(`${root.value}/knowledge/search`, { query: searchText.value })
    notice.value = `在当前项目找到 ${results.value.length} 条内容。`
  } catch (cause) { error.value = (cause as Error).message }
}
function editBible(item: ProjectBible) {
  bibleEditingId.value = item.id
  bibleTitle.value = item.title
  bibleContent.value = item.content
}
function resetBible() {
  bibleEditingId.value = ''
  bibleTitle.value = ''
  bibleContent.value = ''
}
async function saveBible() {
  busy.value = true; error.value = ''
  try {
    if (selectedBible.value) {
      await api.patch(`${root.value}/bibles/${selectedBible.value.id}`, {
        expected_version: selectedBible.value.version,
        title: bibleTitle.value,
        content: bibleContent.value,
      })
      notice.value = '项目圣经已更新。'
    } else {
      await api.post(`${root.value}/bibles`, { title: bibleTitle.value, content: bibleContent.value })
      notice.value = '项目圣经条目已创建。'
    }
    resetBible()
    await load()
  } catch (cause) { error.value = (cause as Error).message } finally { busy.value = false }
}
async function removeBible(item: ProjectBible) {
  try {
    await api.delete(`${root.value}/bibles/${item.id}`, { expected_version: item.version })
    if (bibleEditingId.value === item.id) resetBible()
    notice.value = '项目圣经条目已删除。'
    await load()
  } catch (cause) { error.value = (cause as Error).message }
}

onMounted(load)
</script>

<template>
  <div class="section-head"><div><div class="section-kicker">Story Studio</div><h1>故事工作室</h1><p>导入原作、维护人工确认的世界观事实，并建立项目级语义检索。</p></div></div>
  <div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>

  <div class="split"><div class="stack"><section v-if="canEdit" class="panel panel-pad"><h2>导入故事</h2><form @submit.prevent="importStory"><div class="field"><label>标题</label><input v-model="title" required maxlength="200" placeholder="故事标题" /></div><div class="field"><label>故事正文</label><textarea v-model="content" required minlength="20" rows="9" placeholder="粘贴真实故事或小说片段…"></textarea></div><button class="btn btn-primary" :disabled="busy"><Upload :size="15" />导入原作</button></form></section><section class="panel panel-pad"><div class="between"><h2>原作列表</h2><span class="muted">{{ stories.length }} 份</span></div><div v-if="stories.length" class="list"><button v-for="story in stories" :key="story.id" class="list-row" style="text-align:left;color:inherit" :class="{active:selectedId === story.id}" @click="selectedId = story.id"><div><strong>{{ story.title }}</strong><br><small>{{ new Date(story.created_at).toLocaleDateString('zh-CN') }}</small></div><BookOpen :size="15" /></button></div><div v-else class="empty"><div><strong>暂无原作</strong><p>导入故事后，角色与分镜会围绕它展开。</p></div></div></section></div>
  <div class="stack"><section class="panel panel-pad"><div class="between"><h2>{{ selected?.title || '选择故事' }}</h2><button v-if="selected && canEdit" class="btn btn-sm" :disabled="busy" @click="indexStory">建立索引</button></div><div v-if="selected" class="story-text" style="max-height:500px;overflow:auto">{{ selected.content }}</div><div v-else class="empty"><div><strong>选择一份故事</strong><p>查看正文并建立知识索引。</p></div></div></section><section class="panel panel-pad"><h2>项目知识检索</h2><form class="row" @submit.prevent="search"><input v-model="searchText" required placeholder="检索人物、事件或世界规则…" /><button class="btn" aria-label="搜索"><Search :size="16" /></button></form><div v-if="results.length" class="list" style="margin-top:16px"><div v-for="(result,index) in results" :key="index" class="subtle-panel" style="padding:13px"><div class="mini-label">来源 {{ result.source_id.slice(0,8) }} · {{ result.score.toFixed(2) }}</div><p class="story-text" style="margin:7px 0 0">{{ result.text }}</p></div></div></section></div></div>

  <div class="split" style="margin-top:18px">
    <section class="panel panel-pad"><div class="between"><div><h2>项目圣经</h2><p class="muted" style="font-size:12px">人工确认的世界观、力量体系、地点规则和不可违背事实；Director 会作为结构化事实读取。</p></div><span class="status">{{ bibles.length }}</span></div><div v-if="bibles.length" class="list"><div v-for="item in bibles" :key="item.id" class="list-row"><button style="text-align:left;color:inherit;flex:1" @click="editBible(item)"><strong>{{ item.title }}</strong><br><small>版本 {{ item.version }} · {{ item.content.slice(0,70) }}</small></button><button v-if="canEdit" class="icon-button" title="删除条目" @click="removeBible(item)"><Trash2 :size="14" /></button></div></div><div v-else class="empty"><div><strong>暂无项目圣经</strong><p>把必须长期保持一致的事实放在这里，不依赖模型自己记忆。</p></div></div></section>
    <section v-if="canEdit" class="panel panel-pad"><div class="between"><h2>{{ selectedBible ? '编辑项目圣经' : '新增项目圣经' }}</h2><button v-if="selectedBible" class="btn btn-sm" @click="resetBible">新建</button></div><form @submit.prevent="saveBible"><div class="field"><label>标题</label><input v-model="bibleTitle" required maxlength="200" placeholder="例如：修炼体系 / 城市规则 / 不可变设定" /></div><div class="field"><label>Canonical 内容</label><textarea v-model="bibleContent" required rows="9" maxlength="200000" placeholder="只写已经确认的事实；不确定内容不要写入。"></textarea></div><button class="btn btn-primary" :disabled="busy"><Plus :size="15" />{{ selectedBible ? '保存版本' : '新增条目' }}</button></form></section>
  </div>
</template>
