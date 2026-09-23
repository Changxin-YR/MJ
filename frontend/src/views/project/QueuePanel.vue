<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import { api } from '../../api'
import StatusPill from '../../components/StatusPill.vue'
import type { Job, Project } from '../../types'
const props = defineProps<{ project: Project; eventRevision: number }>()
const jobs = ref<Job[]>([]); const filter = ref('ALL'); const error = ref('')
const shown = computed(() => filter.value === 'ALL' ? jobs.value : jobs.value.filter(j => j.status === filter.value))
async function load() { try { jobs.value = await api.get(`/projects/${props.project.id}/generation-jobs`) } catch (cause) { error.value = (cause as Error).message } }
watch(() => props.eventRevision, load); onMounted(load)
</script>
<template>
  <div class="section-head"><div><div class="section-kicker">Generation Queue</div><h1>生成队列</h1><p>任务状态来自 MySQL；实时事件用于提醒，刷新后仍可恢复真实进度。</p></div><button class="btn" @click="load"><RefreshCcw :size="15" />刷新状态</button></div>
  <div v-if="error" class="error" style="margin-bottom:16px">{{ error }}</div><div class="toolbar" style="margin-bottom:17px"><button v-for="status in ['ALL','QUEUED','RUNNING','SUCCEEDED','FAILED']" :key="status" class="btn btn-sm" :class="filter === status ? 'btn-primary' : ''" @click="filter = status">{{ status }}</button></div>
  <div v-if="shown.length" class="table-wrap"><table class="data-table"><thead><tr><th>任务</th><th>类型</th><th>供应商</th><th>状态</th><th>检查</th><th>重试</th><th>预计 / 实际成本</th><th>创建时间</th></tr></thead><tbody><tr v-for="job in shown" :key="job.id"><td class="mono">{{ job.id.slice(0,8) }}</td><td>{{ job.kind }}</td><td>{{ job.provider }} / {{ job.model }}</td><td><StatusPill :status="job.status" /></td><td>{{ job.inspection ? `${job.inspection.status} · ${job.inspection.method}` : "—" }}</td><td>{{ job.retry_count }}</td><td>{{ job.estimated_cost.toFixed(2) }} / {{ job.actual_cost.toFixed(2) }}</td><td>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</td></tr></tbody></table></div><div v-else class="empty"><div><strong>暂无任务</strong><p>在分镜工作室或 Director Studio 发起生成。</p></div></div>
</template>
