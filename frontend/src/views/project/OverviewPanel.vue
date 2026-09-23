<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ArrowRight, BookOpen, Clapperboard, Film, UserRound } from 'lucide-vue-next'
import { api } from '../../api'
import type { Character, Episode, Job, Project, Story } from '../../types'
const props = defineProps<{ project: Project; canEdit: boolean; eventRevision: number }>()
const stories = ref<Story[]>([]); const characters = ref<Character[]>([]); const episodes = ref<Episode[]>([]); const jobs = ref<Job[]>([])
async function load() { const root = `/projects/${props.project.id}`; [stories.value, characters.value, episodes.value, jobs.value] = await Promise.all([api.get<Story[]>(`${root}/stories`), api.get<Character[]>(`${root}/characters`), api.get<Episode[]>(`${root}/episodes`), api.get<Job[]>(`${root}/generation-jobs`)]) }
onMounted(load); watch(() => props.eventRevision, load)
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">项目概览</div><h1>{{ project.name }}</h1><p>{{ project.description || '从故事出发，逐镜构建可审核、可溯源的漫剧。' }}</p></div><span class="status status-active">{{ project.role }}</span></div>
  <div class="grid-three" style="margin-bottom:24px">
    <div class="panel panel-pad"><div class="row"><BookOpen :size="17" /><span class="mini-label">故事来源</span></div><div class="metric">{{ stories.length }}</div><small>已导入原作</small></div>
    <div class="panel panel-pad"><div class="row"><UserRound :size="17" /><span class="mini-label">角色</span></div><div class="metric">{{ characters.length }}</div><small>含草稿与正式版本</small></div>
    <div class="panel panel-pad"><div class="row"><Film :size="17" /><span class="mini-label">剧集</span></div><div class="metric">{{ episodes.length }}</div><small>正在制作</small></div>
  </div>
  <div class="split">
    <div class="panel panel-pad"><div class="between"><h2>制作路径</h2><Clapperboard :size="19" color="#a5e7e8" /></div><div class="list"><RouterLink :to="`/projects/${project.id}/story`" class="list-row"><div><strong>01 · 导入故事</strong><br><small>建立可靠的原作事实源</small></div><ArrowRight :size="16" /></RouterLink><RouterLink :to="`/projects/${project.id}/characters`" class="list-row"><div><strong>02 · 定义角色</strong><br><small>维护角色 DNA 和正式版本</small></div><ArrowRight :size="16" /></RouterLink><RouterLink :to="`/projects/${project.id}/storyboard`" class="list-row"><div><strong>03 · 制作分镜</strong><br><small>以镜头为最小生产单位</small></div><ArrowRight :size="16" /></RouterLink><RouterLink :to="`/projects/${project.id}/timeline`" class="list-row"><div><strong>04 · 审核与成片</strong><br><small>按时间线渲染最终视频</small></div><ArrowRight :size="16" /></RouterLink></div></div>
    <div class="panel panel-pad"><h2>当前状态</h2><div class="divider"></div><div class="between"><span class="muted">预算上限</span><strong>{{ project.budget_limit.toFixed(2) }}</strong></div><div class="between" style="margin-top:12px"><span class="muted">已使用</span><strong>{{ project.budget_used.toFixed(2) }}</strong></div><div class="between" style="margin-top:12px"><span class="muted">已预留</span><strong>{{ project.budget_reserved.toFixed(2) }}</strong></div><div class="divider"></div><div class="between"><span class="muted">生成任务</span><strong>{{ jobs.length }}</strong></div><p class="muted" style="font-size:12px;line-height:1.6;margin-top:23px">任务、审批和审核结果会在各工作台中显示。所有实际状态以服务端记录为准。</p></div>
  </div>
</template>
