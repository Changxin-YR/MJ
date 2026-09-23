import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, setAccessToken } from '../api'

export interface User { id: string; email: string; display_name: string }
interface AuthResult { access_token: string; user: User }

export const useAuth = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const ready = ref(false)
  async function restore() {
    if (ready.value) return
    try {
      const result = await api.post<AuthResult>('/auth/refresh')
      setAccessToken(result.access_token)
      user.value = result.user
    } catch { user.value = null }
    ready.value = true
  }
  async function login(email: string, password: string) {
    const result = await api.post<AuthResult>('/auth/login', { email, password })
    setAccessToken(result.access_token)
    user.value = result.user
    ready.value = true
  }
  async function register(email: string, password: string, display_name: string) {
    const result = await api.post<AuthResult>('/auth/register', { email, password, display_name })
    setAccessToken(result.access_token)
    user.value = result.user
    ready.value = true
  }
  async function logout() {
    try { await api.post('/auth/logout') } finally { setAccessToken(null); user.value = null; ready.value = true }
  }
  return { user, ready, restore, login, register, logout }
})
