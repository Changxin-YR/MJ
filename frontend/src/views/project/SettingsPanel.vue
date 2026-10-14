<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../../api'
import type { Project } from '../../types'
const props = defineProps<{ project: Project; canEdit: boolean }>()
const emit = defineEmits<{ projectUpdated: [] }>()
const budget = ref(props.project.budget_limit); const style = ref(String(props.project.settings.style || '')); const email = ref(''); const role = ref('VIEWER'); const workspaceEmail = ref(''); const error = ref(''); const notice = ref('')
const saved = ref(JSON.stringify({ budget: budget.value, style: style.value }))
const dirty = computed(() => JSON.stringify({ budget: budget.value, style: style.value }) !== saved.value)
const editVersion = ref(props.project.version)
const baseSettings = ref({ ...props.project.settings })
const latestProject = ref<Project | null>(null)
const saving = ref(false)
watch(() => props.project, project => {
  if (dirty.value || saving.value) return
  budget.value = project.budget_limit; style.value = String(project.settings.style || '')
  saved.value = JSON.stringify({ budget: budget.value, style: style.value }); editVersion.value = project.version
  baseSettings.value = { ...project.settings }
})
async function save() {
  if (saving.value) return
  saving.value = true; error.value = ''; latestProject.value = null
  const submitted = { budget: budget.value, style: style.value }
  try {
    const updated = await api.patch<Project>(`/projects/${props.project.id}/settings`, { expected_version: editVersion.value, settings: { ...baseSettings.value, style: submitted.style }, budget_limit: props.project.role === 'OWNER' ? submitted.budget : undefined })
    saved.value = JSON.stringify(submitted); editVersion.value = updated.version; baseSettings.value = { ...updated.settings }
    notice.value = '设置已保存。'; emit('projectUpdated')
  } catch (cause) {
    error.value = (cause as Error).message
    if ((cause as { code?: string }).code === 'RESOURCE_VERSION_CONFLICT') {
      try { latestProject.value = await api.get<Project>(`/projects/${props.project.id}`) }
      catch (refreshError) { error.value = (refreshError as Error).message }
    }
  } finally { saving.value = false }
}
function acceptLatest(useDraft: boolean) {
  const latest = latestProject.value
  if (!latest || saving.value) return
  editVersion.value = latest.version; baseSettings.value = { ...latest.settings }; latestProject.value = null
  if (useDraft) void save()
  else {
    budget.value = latest.budget_limit; style.value = String(latest.settings.style || '')
    saved.value = JSON.stringify({ budget: budget.value, style: style.value }); error.value = ''; notice.value = '已载入服务器设置。'
  }
}
async function addWorkspaceMember() { try { await api.post(`/workspaces/${props.project.workspace_id}/members`, { email: workspaceEmail.value, role: 'MEMBER' }); workspaceEmail.value = ''; notice.value = '已加入工作空间。' } catch (cause) { error.value = (cause as Error).message } }
async function addMember() { try { await api.post(`/projects/${props.project.id}/members`, { email: email.value, role: role.value }); email.value = ''; notice.value = '项目成员已添加。' } catch (cause) { error.value = (cause as Error).message } }
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Project Settings</div><h1>项目设置</h1><p>管理创作风格、预算边界与项目成员。</p></div></div><div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div v-if="notice" class="notice" style="margin-bottom:16px">{{ notice }}</div>
  <div v-if="latestProject" class="notice" role="alert" style="margin-bottom:16px"><p>设置已被更新。服务器当前风格：{{ latestProject.settings.style || '未设置' }}；预算：{{ latestProject.budget_limit }}。当前草稿已保留。</p><div class="toolbar"><button class="btn" :disabled="saving" @click="acceptLatest(false)">载入服务器设置</button><button class="btn btn-primary" :disabled="saving" @click="acceptLatest(true)">用当前草稿重试保存</button></div></div><div class="split"><section class="panel panel-pad"><h2>制作参数</h2><form @submit.prevent="save"><div class="field"><label>视觉风格</label><input v-model="style" :disabled="!canEdit" placeholder="例如：赛博朋克水彩" /></div><div class="field"><label>项目预算上限</label><input v-model.number="budget" type="number" min="0" step="0.01" :disabled="project.role !== 'OWNER'" /></div><div class="between"><span class="muted" style="font-size:12px">已使用 {{ project.budget_used.toFixed(2) }} · 已预留 {{ project.budget_reserved.toFixed(2) }}</span><span class="mini-label">版本 {{ project.version }}</span></div><button v-if="canEdit" class="btn btn-primary" :disabled="saving" style="margin-top:20px">保存设置</button></form></section><div class="stack"><section v-if="project.role === 'OWNER'" class="panel panel-pad"><h2>邀请工作空间成员</h2><form class="row" @submit.prevent="addWorkspaceMember"><input v-model="workspaceEmail" type="email" required placeholder="已注册用户的邮箱" /><button class="btn">添加</button></form><p class="muted" style="font-size:12px;margin-top:12px">用户需先加入工作空间，才能被授予项目角色。</p></section><section v-if="project.role === 'OWNER'" class="panel panel-pad"><h2>授予项目角色</h2><form @submit.prevent="addMember"><div class="field"><label>邮箱</label><input v-model="email" type="email" required /></div><div class="field"><label>角色</label><select v-model="role"><option value="DIRECTOR">Director</option><option value="EDITOR">Editor</option><option value="REVIEWER">Reviewer</option><option value="VIEWER">Viewer</option></select></div><button class="btn btn-primary">添加成员</button></form></section></div></div>
</template>
