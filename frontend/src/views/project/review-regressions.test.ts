import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { api } from '../../api'
import CharacterPanel from './CharacterPanel.vue'
import DirectorPanel from './DirectorPanel.vue'
import StoryboardPanel from './StoryboardPanel.vue'
import ReviewPanel from './ReviewPanel.vue'
import TimelinePanel from './TimelinePanel.vue'
import SettingsPanel from './SettingsPanel.vue'
import type { Project, Shot } from '../../types'

vi.mock('../../api', () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), blob: vi.fn() }, ApiError: class extends Error {} }))
const project: Project = { id: 'p', workspace_id: 'w', name: 'Project', description: '', version: 1, settings: { style: 'original' }, budget_limit: 100, budget_reserved: 0, budget_used: 0, role: 'OWNER' }
const props = { project, canEdit: true, canReview: true, eventRevision: 0 }
const wrappers: VueWrapper[] = []
const shot = (id: string, overrides: Partial<Shot> = {}): Shot => ({ id, episode_id: 'e', scene_id: 's', shot_no: 1, shot_type: 'medium', camera_angle: 'eye-level', camera_movement: 'static', duration: 6, description: id, action: '', dialogue: '', emotion: '', character_ids: [], prompt: '', negative_prompt: '', current_image_asset_id: null, current_video_asset_id: null, current_audio_asset_id: null, inspection_json: null, status: 'DRAFT', version: 1, ...overrides })
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { promise, resolve } }
function hierarchy(items: Shot[]) {
  vi.mocked(api.get).mockImplementation(async (path: string) => {
    if (path.endsWith('/episodes')) return [{ id: 'e', episode_no: 1, title: 'Episode' }] as never
    if (path.endsWith('/scenes')) return [{ id: 's', scene_no: 1, heading: 'Scene' }] as never
    if (path.endsWith('/shots')) return items as never
    return [] as never
  })
}
function render(component: Parameters<typeof mount>[0]): VueWrapper { const wrapper = mount(component, { props, global: { stubs: { RouterLink: true } } }); wrappers.push(wrapper); return wrapper }
beforeEach(() => { vi.resetAllMocks(); vi.stubGlobal('confirm', vi.fn(() => true)); URL.revokeObjectURL = vi.fn(); vi.mocked(api.get).mockResolvedValue([]); vi.mocked(api.blob).mockImplementation(async path => `blob:${path}`) })
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })

describe('review regressions', () => {
  it('loads the newest character draft and resets fields for a new character', async () => {
    vi.mocked(api.get).mockImplementation(async path => path.endsWith('/characters') ? [{ id: 'c', name: 'Mira' }] as never : { id: 'c', name: 'Mira', active_version_id: null, version: 2, versions: [{ id: 'v2', version_no: 2, background: 'newest background', status: 'DRAFT', dna: { face: 'new freckles' } }, { id: 'v1', version_no: 1, background: 'old background', status: 'DRAFT', dna: { face: 'old' } }] } as never)
    const wrapper = render(CharacterPanel); await flushPromises()
    await wrapper.find('.list-row').trigger('click'); await flushPromises()
    expect(wrapper.findAll('textarea').some(t => t.element.value === 'newest background')).toBe(true)
    await wrapper.findAll('button').find(b => b.text().includes('新建其他角色'))!.trigger('click')
    expect(wrapper.findAll('textarea').every(t => !t.element.value)).toBe(true)
  })

  it('preserves an edited shot and its version across background refreshes', async () => {
    const items = [shot('first')]; hierarchy(items)
    const wrapper = render(StoryboardPanel); await flushPromises()
    await wrapper.find('.shot-card').trigger('click')
    await wrapper.get('#shot-description').setValue('unsaved local draft')
    items[0] = shot('first', { description: 'server update', version: 2 })
    await wrapper.setProps({ eventRevision: 1 }); await flushPromises()
    expect((wrapper.get('#shot-description').element as HTMLTextAreaElement).value).toBe('unsaved local draft')
    vi.mocked(api.patch).mockResolvedValue(shot('first', { description: 'unsaved local draft', version: 3 }))
    const form = wrapper.find('form')
    if (form.exists()) await form.trigger('submit')
    else await wrapper.findAll('button').find(b => b.text().includes('保存镜头'))!.trigger('click')
    await flushPromises()
    expect(api.patch).toHaveBeenCalledWith('/projects/p/shots/first', expect.objectContaining({ description: 'unsaved local draft', expected_version: 1 }))
  })

  it('asks before switching the section parameter of the same route', async () => {
    hierarchy([shot('first')])
    const Shell = defineComponent({ components: { StoryboardPanel }, setup: () => ({ props }), template: '<StoryboardPanel v-bind="props" />' })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id/:section', component: Shell }] })
    await router.push('/projects/p/storyboard'); await router.isReady()
    const wrapper = mount(RouterView, { global: { plugins: [router] } }); wrappers.push(wrapper); await flushPromises()
    await wrapper.find('.shot-card').trigger('click'); await wrapper.get('#shot-description').setValue('unsaved')
    vi.mocked(window.confirm).mockReturnValue(false)
    await router.push('/projects/p/review')
    expect(window.confirm).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.params.section).toBe('storyboard')
  })

  it('replaces the thumbnail when a shot receives a different image asset', async () => {
    const items = [shot('first', { current_image_asset_id: 'image1' })]; hierarchy(items)
    const wrapper = render(StoryboardPanel); await flushPromises()
    expect(wrapper.get('.shot-thumb img').attributes('src')).toContain('image1')
    items[0] = shot('first', { current_image_asset_id: 'image2', version: 2 })
    await wrapper.setProps({ eventRevision: 1 }); await flushPromises()
    expect(wrapper.get('.shot-thumb img').attributes('src')).toContain('image2')
  })

  it('keeps the newest complete Director refresh when requests finish out of order', async () => {
    hierarchy([shot('first')]); const wrapper = render(DirectorPanel); await flushPromises()
    const slow = deferred<Shot[]>(); let shotReads = 0
    vi.mocked(api.get).mockImplementation(async path => {
      if (path.endsWith('/episodes')) return [{ id: 'e', title: 'Episode' }] as never
      if (path.endsWith('/scenes')) return [{ id: 's' }] as never
      if (path.endsWith('/shots')) return (++shotReads === 1 ? await slow.promise : [shot('new')]) as never
      return [] as never
    })
    const refresh = wrapper.findAll('button').find(b => b.text() === '刷新')!
    await refresh.trigger('click'); await flushPromises(); await refresh.trigger('click'); await flushPromises()
    slow.resolve([shot('old')]); await flushPromises()
    await wrapper.find('select').setValue('GENERATE_IMAGE')
    expect(wrapper.text()).not.toContain('2 个镜头')
    expect(wrapper.findAll('option').some(o => o.text().includes('new'))).toBe(true)
    expect(wrapper.findAll('option').some(o => o.text().includes('old'))).toBe(false)
  })

  it('renders a persisted PLANNING run before structure counts exist', async () => {
    const run = { id: 'r', request: 'produce', status: 'PLANNING', current_node: 'Plan', decision_summary: '', warnings: [], production: { mode: 'AUTOPRODUCE', stage: 'PLANNING' }, retrieved_sources: [], tool_calls: [], generation_jobs: [], pending_actions: [], cost: 0, trace_id: 'trace' }
    vi.mocked(api.get).mockImplementation(async path => path.endsWith('/director/runs') ? [run] as never : [] as never)
    const wrapper = render(DirectorPanel); await flushPromises()
    expect(wrapper.text()).toContain('produce'); expect(wrapper.text()).toMatch(/角色\s*0/)
  })

  it('discards stale review media when selection changes during loading', async () => {
    hierarchy([shot('A', { status: 'REVIEW_REQUIRED', current_video_asset_id: 'videoA' }), shot('B', { status: 'REVIEW_REQUIRED', current_video_asset_id: 'videoB' })])
    const slow = deferred<string>(); vi.mocked(api.blob).mockImplementation(async path => path.includes('videoA') ? await slow.promise : 'blob:videoB')
    const wrapper = render(ReviewPanel); await flushPromises()
    await wrapper.findAll('.list-row')[0]!.trigger('click'); await flushPromises()
    await wrapper.findAll('.list-row')[1]!.trigger('click'); await flushPromises()
    slow.resolve('blob:videoA'); await flushPromises()
    expect(wrapper.get('video').attributes('src')).toBe('blob:videoB')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:videoA')
  })

  it('offers a reason field for UNVERIFIED video and binds approval to that reason', async () => {
    hierarchy([shot('A', { status: 'REVIEW_REQUIRED', current_video_asset_id: 'videoA', inspection_json: { status: 'UNVERIFIED', issues: [], checks: {}, method: 'unavailable' } })])
    const wrapper = render(ReviewPanel); await flushPromises(); await wrapper.find('.list-row').trigger('click'); await flushPromises()
    const approve = wrapper.findAll('button').find(b => b.text() === '通过')!
    expect(approve.attributes('disabled')).toBeDefined()
    await wrapper.get('#review-reason').setValue('完整播放后人工确认')
    await approve.trigger('click'); await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/projects/p/shots/A/transition', expect.objectContaining({ review_reason: '完整播放后人工确认' }))
  })

  it('requests a fresh reason when a previously reviewed video returns for approval', async () => {
    hierarchy([shot('A', { status: 'REVIEW_REQUIRED', current_video_asset_id: 'videoA', inspection_json: { status: 'UNVERIFIED', issues: [], checks: {}, method: 'unavailable', review_override: { actor_id: 'reviewer', video_asset_id: 'videoA', reason: 'earlier approval', reviewed_at: '' } } })])
    const wrapper = render(ReviewPanel); await flushPromises(); await wrapper.find('.list-row').trigger('click'); await flushPromises()
    const approve = wrapper.findAll('button').find(b => b.text() === '通过')!
    expect(approve.attributes('disabled')).toBeDefined()
    await wrapper.get('#review-reason').setValue('重新审核后确认当前视频')
    await approve.trigger('click'); await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/projects/p/shots/A/transition', expect.objectContaining({ review_reason: '重新审核后确认当前视频' }))
  })

  it('hides the reason field after the current video has been approved', async () => {
    hierarchy([shot('A', { status: 'APPROVED', current_video_asset_id: 'videoA', inspection_json: { status: 'UNVERIFIED', issues: [], checks: {}, method: 'unavailable', review_override: { actor_id: 'reviewer', video_asset_id: 'videoA', reason: 'confirmed', reviewed_at: '' } } })])
    const wrapper = render(ReviewPanel); await flushPromises(); await wrapper.find('.list-row').trigger('click'); await flushPromises()
    expect(wrapper.find('#review-reason').exists()).toBe(false)
    expect(wrapper.findAll('button').find(b => b.text() === '锁定')!.attributes('disabled')).toBeUndefined()
  })

  it('clears the review reason when background refresh replaces the video', async () => {
    const items = [shot('A', { status: 'REVIEW_REQUIRED', current_video_asset_id: 'videoA', inspection_json: { status: 'UNVERIFIED', issues: [], checks: {}, method: 'unavailable' } })]; hierarchy(items)
    const wrapper = render(ReviewPanel); await flushPromises(); await wrapper.find('.list-row').trigger('click'); await flushPromises()
    await wrapper.get('#review-reason').setValue('reviewed video A')
    items[0] = { ...items[0]!, current_video_asset_id: 'videoB', version: 2 }
    await wrapper.setProps({ eventRevision: 1 }); await flushPromises()
    expect((wrapper.get('#review-reason').element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.findAll('button').find(b => b.text() === '通过')!.attributes('disabled')).toBeDefined()
  })

  it('preserves settings drafts when project events refresh the parent', async () => {
    const wrapper = render(SettingsPanel)
    await wrapper.find('input').setValue('unsaved style')
    await wrapper.setProps({ project: { ...project, version: 2, budget_used: 1 } })
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe('unsaved style')
  })

  it('allows an explicit settings conflict retry without discarding the draft', async () => {
    const wrapper = render(SettingsPanel)
    await wrapper.find('input').setValue('my draft')
    await wrapper.setProps({ project: { ...project, version: 2, settings: { style: 'other editor' } } })
    const conflict = Object.assign(new Error('Settings changed'), { code: 'RESOURCE_VERSION_CONFLICT' })
    vi.mocked(api.patch).mockRejectedValueOnce(conflict).mockResolvedValueOnce({ ...project, version: 3, settings: { style: 'my draft' } })
    vi.mocked(api.get).mockResolvedValue({ ...project, version: 2, settings: { style: 'other editor', language: 'zh' } })
    await wrapper.find('form').trigger('submit'); await flushPromises()
    expect(api.patch).toHaveBeenNthCalledWith(1, '/projects/p/settings', expect.objectContaining({ expected_version: 1 }))
    expect(wrapper.text()).toContain('other editor')
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe('my draft')
    await wrapper.findAll('button').find(b => b.text() === '用当前草稿重试保存')!.trigger('click'); await flushPromises()
    expect(api.patch).toHaveBeenNthCalledWith(2, '/projects/p/settings', expect.objectContaining({ expected_version: 2, settings: { style: 'my draft', language: 'zh' } }))
  })

  it('removes the old final video after synchronizing the timeline', async () => {
    const timeline = { id: 't', episode_id: 'e', status: 'COMPLETED', version: 1, final_asset_id: 'old-final', tracks: [], items: [] }
    vi.mocked(api.get).mockImplementation(async path => path.endsWith('/episodes') ? [{ id: 'e', episode_no: 1, title: 'Episode' }] as never : timeline as never)
    vi.mocked(api.post).mockResolvedValue({ ...timeline, status: 'READY', version: 2, final_asset_id: null })
    const wrapper = render(TimelinePanel); await flushPromises(); expect(wrapper.find('video').exists()).toBe(true)
    await wrapper.findAll('button').find(b => b.text().includes('同步'))!.trigger('click'); await flushPromises()
    expect(wrapper.find('video').exists()).toBe(false)
    expect(URL.revokeObjectURL).toHaveBeenCalled()
  })

  it('ignores a stale timeline refresh that completes after synchronization', async () => {
    const timeline = { id: 't', episode_id: 'e', status: 'COMPLETED', version: 1, final_asset_id: 'old-final', tracks: [], items: [] }
    vi.mocked(api.get).mockImplementation(async path => path.endsWith('/episodes') ? [{ id: 'e', episode_no: 1, title: 'Episode' }] as never : timeline as never)
    const wrapper = render(TimelinePanel); await flushPromises()
    const slow = deferred<typeof timeline>()
    vi.mocked(api.get).mockImplementation(async () => await slow.promise as never)
    await wrapper.findAll('button').find(b => b.text() === '刷新')!.trigger('click'); await flushPromises()
    vi.mocked(api.post).mockResolvedValue({ ...timeline, status: 'READY', version: 2, final_asset_id: null })
    await wrapper.findAll('button').find(b => b.text().includes('同步'))!.trigger('click'); await flushPromises()
    slow.resolve(timeline); await flushPromises()
    expect(wrapper.find('video').exists()).toBe(false)
  })
})
