import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { getAccessToken, refreshAccessToken } from '../api'

export function useProjectEvents(projectId: Ref<string>, onEvent: (name: string) => void) {
  const connected = ref(false)
  let controller: AbortController | null = null
  let stopped = false

  async function connect(id: string) {
    controller?.abort()
    if (!id) return
    controller = new AbortController()
    const signal = controller.signal
    while (!stopped && !signal.aborted) {
      try {
        const response = await fetch(`/api/v1/projects/${id}/events`, { headers: { Authorization: `Bearer ${getAccessToken() || ''}` }, signal })
        if (response.status === 401) {
          connected.value = false
          const token = await refreshAccessToken()
          if (!token) throw new Error('Session expired')
          continue
        }
        if (!response.ok || !response.body) throw new Error('Stream unavailable')
        connected.value = true
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (!signal.aborted) {
          const chunk = await reader.read()
          if (chunk.done) { connected.value = false; break }
          buffer += decoder.decode(chunk.value, { stream: true })
          const parts = buffer.split('\n\n')
          buffer = parts.pop() || ''
          for (const part of parts) {
            const name = part.split('\n').find(line => line.startsWith('event: '))?.slice(7)
            if (name) onEvent(name)
          }
        }
      } catch { connected.value = false }
      if (!signal.aborted && !stopped) await new Promise(resolve => setTimeout(resolve, 3000))
    }
  }
  watch(projectId, id => { stopped = false; void connect(id) }, { immediate: true })
  onBeforeUnmount(() => { stopped = true; controller?.abort() })
  return { connected }
}
