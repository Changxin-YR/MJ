import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { api } from '../api'
import { useProjectEvents } from './useProjectEvents'

vi.mock('../api', () => ({ api: { get: vi.fn() }, getAccessToken: () => 'token' }))
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('refreshes authentication and reconnects after an expired SSE token', async () => {
  vi.useFakeTimers()
  const fetchMock = vi.fn().mockResolvedValueOnce({ status: 401 }).mockImplementation((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')))))
  vi.stubGlobal('fetch', fetchMock)
  const wrapper = mount(defineComponent({ setup() { useProjectEvents(ref('p'), () => {}); return () => null } }))
  try {
    await flushPromises()
    expect(api.get).toHaveBeenCalledWith('/auth/me')
    await vi.advanceTimersByTimeAsync(3000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  } finally { wrapper.unmount() }
})
