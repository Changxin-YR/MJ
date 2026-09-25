<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, Clapperboard, LogOut, Plus } from 'lucide-vue-next'
import { api } from '../api'
import { useAuth } from '../stores/auth'
import type { Project, Workspace } from '../types'
const auth = useAuth(); const router = useRouter()
const workspaces = ref<Workspace[]>([]); const projects = ref<Project[]>([])
const activeId = ref(''); const newWorkspace = ref(''); const newProject = ref(''); const showWorkspaceForm = ref(false); const showProjectForm = ref(false); const error = ref(''); const loading = ref(true)
const active = computed(() => workspaces.value.find(w => w.id === activeId.value))
async function loadWorkspaces() {
  try { workspaces.value = await api.get('/workspaces'); if (!activeId.value && workspaces.value.length) activeId.value = workspaces.value[0].id; await loadProjects() }
  catch (cause) { error.value = (cause as Error).message }
  finally { loading.value = false }
}
async function loadProjects() { projects.value = activeId.value ? await api.get(`/workspaces/${activeId.value}/projects`) : [] }
async function selectWorkspace(id: string) { activeId.value = id; await loadProjects() }
async function createWorkspace() {
  try { const workspace = await api.post<Workspace>('/workspaces', { name: newWorkspace.value }); workspaces.value.push(workspace); activeId.value = workspace.id; newWorkspace.value = ''; showWorkspaceForm.value = false; projects.value = [] }
  catch (cause) { error.value = (cause as Error).message }
}
async function createProject() {
  try { const project = await api.post<Project>(`/workspaces/${activeId.value}/projects`, { name: newProject.value }); newProject.value = ''; showProjectForm.value = false; await router.push(`/projects/${project.id}/overview`) }
  catch (cause) { error.value = (cause as Error).message }
}
async function logout() { await auth.logout(); await router.push('/login') }
onMounted(loadWorkspaces)
</script>
<template>
  <div class="dashboard">
    <header class="topbar"><div class="brand"><span class="brand-mark"><Clapperboard :size="19" /></span>FrameForge AI</div><div class="row"><span class="muted" style="font-size:13px">{{ auth.user?.display_name }}</span><button class="icon-button" title="退出登录" @click="logout"><LogOut :size="17" /></button></div></header>
    <main class="dashboard-main">
      <div class="section-head"><div><div class="section-kicker">制作空间</div><h1>你的工作台</h1><p>进入项目，继续从故事到成片的制作流程。</p></div><button class="btn" @click="showWorkspaceForm = !showWorkspaceForm"><Plus :size="15" />新建工作空间</button></div>
      <div v-if="error" class="error" style="margin-bottom:18px">{{ error }}</div>
      <form v-if="showWorkspaceForm" class="panel panel-pad row" style="margin-bottom:22px" @submit.prevent="createWorkspace"><input v-model="newWorkspace" aria-label="工作空间名称" required placeholder="工作空间名称" /><button class="btn btn-primary">创建</button></form>
      <div v-if="loading" class="muted">正在加载…</div>
      <template v-else>
        <div v-if="workspaces.length" class="workspace-rail"><button v-for="workspace in workspaces" :key="workspace.id" class="workspace-tab" :class="{active: workspace.id === activeId}" @click="selectWorkspace(workspace.id)">{{ workspace.name }}</button></div>
        <div v-if="active" class="section-head" style="margin-top:35px"><div><h2>{{ active.name }}的项目</h2><p>当前身份：{{ active.role }}</p></div><button v-if="active.role === 'OWNER'" class="btn btn-primary" @click="showProjectForm = !showProjectForm"><Plus :size="15" />新建项目</button></div>
        <form v-if="showProjectForm" class="panel panel-pad row" style="margin-bottom:22px" @submit.prevent="createProject"><input v-model="newProject" aria-label="项目名称" required placeholder="项目名称，例如：霓虹夜航" /><button class="btn btn-primary">创建</button></form>
        <div v-if="projects.length" class="grid-three"><RouterLink v-for="project in projects" :key="project.id" :to="`/projects/${project.id}/overview`" class="project-card"><div><h3>{{ project.name }}</h3><p>{{ project.description || '从故事开始，逐步完成角色、镜头和成片。' }}</p></div><div class="row"><span class="status">{{ project.role }}</span><ArrowRight :size="17" /></div></RouterLink></div>
        <div v-else class="empty"><div><strong>{{ active ? '还没有项目' : '先创建工作空间' }}</strong><p>{{ active ? '建立项目后即可导入故事并开始制作。' : '工作空间用于管理项目与团队成员。' }}</p></div></div>
      </template>
    </main>
  </div>
</template>
