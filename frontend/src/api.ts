import axios, { AxiosError, type AxiosRequestConfig } from 'axios'

export interface Envelope<T> { success: boolean; data: T; trace_id: string; error?: { code: string; message: string; details: unknown } }
export class ApiError extends Error { constructor(public code: string, message: string, public status: number) { super(message) } }

let accessToken: string | null = null
let refreshing: Promise<string | null> | null = null
export function setAccessToken(token: string | null) { accessToken = token }
export function getAccessToken() { return accessToken }

export function refreshAccessToken(): Promise<string | null> {
  refreshing ||= axios.post<Envelope<{ access_token: string }>>('/api/v1/auth/refresh', {}, { withCredentials: true })
    .then(response => { setAccessToken(response.data.data.access_token); return response.data.data.access_token })
    .catch(() => { setAccessToken(null); return null })
    .finally(() => { refreshing = null })
  return refreshing
}

const client = axios.create({ baseURL: '/api/v1', withCredentials: true })
client.interceptors.request.use(config => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})
client.interceptors.response.use(response => response, async (error: AxiosError<Envelope<unknown>>) => {
  const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined
  if (error.response?.status === 401 && original && !original._retried && !original.url?.includes('/auth/refresh') && !original.url?.includes('/auth/login')) {
    original._retried = true
    const token = await refreshAccessToken()
    if (token) {
      original.headers = { ...original.headers, Authorization: `Bearer ${token}` } as typeof original.headers
      return client.request(original)
    }
  }
  return Promise.reject(error)
})

async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await client.request<Envelope<T>>(config)
    return response.data.data
  } catch (cause) {
    const error = cause as AxiosError<Envelope<unknown>>
    throw new ApiError(error.response?.data?.error?.code || 'NETWORK_ERROR', error.response?.data?.error?.message || error.message, error.response?.status || 0)
  }
}

export const api = {
  get: <T>(url: string) => request<T>({ method: 'GET', url }),
  post: <T>(url: string, data?: unknown) => request<T>({ method: 'POST', url, data }),
  patch: <T>(url: string, data?: unknown) => request<T>({ method: 'PATCH', url, data }),
  delete: <T>(url: string, data?: unknown) => request<T>({ method: 'DELETE', url, data }),
  upload: <T>(url: string, file: File) => {
    const data = new FormData()
    data.append('file', file)
    return request<T>({ method: 'POST', url, data })
  },
  blob: async (url: string) => {
    const response = await client.get(url, { responseType: 'blob' })
    return URL.createObjectURL(response.data as Blob)
  },
}
