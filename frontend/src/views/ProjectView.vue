<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Activity, BookOpen, Clapperboard, ClipboardCheck, Film, FolderOpen, LayoutGrid, ListVideo, LogOut, Settings, ShieldCheck, Sparkles, UserRound, Users, WandSparkles, Waypoints } from 'lucide-vue-next'
import { api } from '../api'
import { useProjectEvents } from '../composables/useProjectEvents'
import { useAuth } from '../stores/auth'
import type { Project } from '../types'
import OverviewPanel from './project/OverviewPanel.vue'
import StoryPanel from './project/StoryPanel.vue'
import CharacterPanel from './project/CharacterPanel.vue'
import EpisodePanel from './project/EpisodePanel.vue'
import StoryboardPanel from './project/StoryboardPanel.vue'
import DirectorPanel from './project/DirectorPanel.vue'
import QueuePanel from './project/QueuePanel.vue'
import AssetsPanel from './project/AssetsPanel.vue'
import ReviewPanel from './project/ReviewPanel.vue'
import TimelinePanel from './project/TimelinePanel.vue'
import AuditPanel from './project/AuditPanel.vue'
import SettingsPanel from './project/SettingsPanel.vue'

const route = useRoute(); const router = useRouter(); const auth = useAuth()
const projectId = computed(() => String(route.params.projectId || ''))
const section = computed(() => String(route.params.section || 'overview'))
const project = ref<Project | null>(null)
const error = ref('')
const eventRevision = ref(0)
const nav = [
  { id: 'overview', label: '项目概览', icon: LayoutGrid },
  { id: 'story', label: '故事工作室', icon: BookOpen },
  { id: 'characters', label: '角色工作室', icon: UserRound },
  { id: 'episodes', label: '剧集与场景', icon: Film },
  { id: 'storyboard', label: '分镜工作室', icon: Clapperboard },
  { id: 'director', label: 'Director Studio', icon: WandSparkles },
  { id: 'queue', label: '生成队列', icon: Activity },
  { id: 'assets', label: '素材库', icon: FolderOpen },
  { id: 'review', label: '审核', icon: ClipboardCheck },
  { id: 'timeline', label: '时间线', icon: ListVideo },
  { id: 'audit', label: '审计与溯源', icon: Waypoints },
  { id: 'settings', label: '设置', icon: Settings },
]
const activePanel = computed(() => ({ overview: OverviewPanel, story: StoryPanel, characters: CharacterPanel, episodes: EpisodePanel, storyboard: StoryboardPanel, director: DirectorPanel, queue: QueuePanel, assets: AssetsPanel, review: ReviewPanel, timeline: TimelinePanel, audit: AuditPanel, settings: SettingsPanel }[section.value] || OverviewPanel))
const canEdit = computed(() => ['OWNER', 'DIRECTOR', 'EDITOR'].includes(project.value?.role || ''))
const canReview = computed(() => ['OWNER', 'DIRECTOR', 'REVIEWER'].includes(project.value?.role || ''))
const canAudit = computed(() => project.value?.role === 'OWNER')
const { connected } = useProjectEvents(projectId, () => { eventRevision.value++; void load() })
async function load() { try { project.value = await api.get<Project>(`/projects/${projectId.value}`) } catch (cause) { error.value = (cause as Error).message } }
async function logout() { await auth.logout(); await router.push('/login') }
watch(projectId, load); onMounted(load)
</script>
<template>
  <div class="project-shell">
    <aside class="sidebar">
      <RouterLink to="/" class="brand"><span class="brand-mark"><Clapperboard :size="19" /></span>FrameForge AI</RouterLink>
      <nav><div class="side-section">制作</div><RouterLink v-for="item in nav.slice(0, 6)" :key="item.id" :to="`/projects/${projectId}/${item.id}`" class="nav-link" :class="{active: section === item.id}"><component :is="item.icon" />{{ item.label }}</RouterLink><div class="side-section">交付</div><RouterLink v-for="item in nav.slice(6)" :key="item.id" :to="`/projects/${projectId}/${item.id}`" class="nav-link" :class="{active: section === item.id}"><component :is="item.icon" />{{ item.label }}</RouterLink></nav>
      <div class="sidebar-foot"><RouterLink to="/" class="nav-link"><Users :size="16" />所有项目</RouterLink></div>
    </aside>
    <div class="project-main">
      <div class="mobile-nav"><RouterLink v-for="item in nav" :key="item.id" :to="`/projects/${projectId}/${item.id}`" :class="{active: section === item.id}">{{ item.label }}</RouterLink></div>
      <header class="project-topbar"><div class="row"><span class="project-title">{{ project?.name || '正在加载…' }}</span><span class="muted">/ {{ nav.find(n => n.id === section)?.label }}</span></div><div class="row"><span class="status" :class="connected ? 'status-active' : ''">{{ connected ? '事件已连接' : '正在连接' }}</span><span class="muted">{{ project?.role }}</span><button class="icon-button" title="退出登录" @click="logout"><LogOut :size="16" /></button></div></header>
      <main class="project-content"><div v-if="error" class="error">{{ error }}</div><component :is="activePanel" v-if="project" :project="project" :can-edit="canEdit" :can-review="canReview" :can-audit="canAudit" :event-revision="eventRevision" @project-updated="load" /></main>
    </div>
  </div>
</template>
