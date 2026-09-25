import { expect, test } from '@playwright/test'

test('Director tool waits for approval before generation', async ({ page, request }) => {
  const email = `director-${Date.now().toString(36)}@example.com`
  await page.goto('/')
  await page.getByRole('button', { name: '没有账户？立即注册' }).click()
  await page.getByLabel('显示名称').fill('Director E2E')
  await page.getByLabel('电子邮箱').fill(email)
  await page.getByLabel('密码').fill('StrongPassword123')
  const registered = page.waitForResponse(response => response.url().endsWith('/auth/register') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '创建账户' }).click()
  const token = (await (await registered).json()).data.access_token as string
  await expect(page.getByRole('heading', { name: '你的工作台' })).toBeVisible()
  const headers = { Authorization: `Bearer ${token}` }
  async function post<T>(path: string, payload: object): Promise<T> {
    const response = await request.post(`/api/v1${path}`, { headers, data: payload })
    expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy()
    return (await response.json()).data as T
  }
  const workspace = await post<{ id: string }>('/workspaces', { name: 'Director Flow' })
  const project = await post<{ id: string }>(`/workspaces/${workspace.id}/projects`, { name: 'Approval Project' })
  const prefix = `/projects/${project.id}`
  const episode = await post<{ id: string }>(`${prefix}/episodes`, { title: 'Pilot' })
  const scene = await post<{ id: string }>(`${prefix}/episodes/${episode.id}/scenes`, { heading: 'EXT. CITY' })
  let shot = await post<{ id: string; version: number }>(`${prefix}/scenes/${scene.id}/shots`, { description: 'One courier on a rainy rooftop', duration: 2 })
  for (const target of ['PLANNED', 'STORYBOARD_READY']) {
    shot = await post<{ id: string; version: number }>(`${prefix}/shots/${shot.id}/transition`, { expected_version: shot.version, target })
  }
  await page.goto(`/projects/${project.id}/director`)
  await expect(page.getByRole('heading', { name: 'Director Studio' })).toBeVisible()
  await page.getByPlaceholder('例如：分析项目上下文，并为选定镜头生成图像').fill('Generate one image for this shot')
  await page.getByLabel('执行意图').selectOption('GENERATE_IMAGE')
  await page.getByLabel('目标镜头').selectOption(shot.id)
  await page.getByRole('button', { name: '运行', exact: true }).click()
  await expect(page.getByText('Director 已提出操作，等待人工批准。')).toBeVisible()
  await expect(page.getByRole('button', { name: '批准' })).toBeVisible()
  await page.getByRole('button', { name: '批准' }).click()
  await expect(page.getByRole('button', { name: '继续运行' })).toBeVisible()
  await page.getByRole('button', { name: '继续运行' }).click()
  await expect(page.getByText('Director 已继续执行。')).toBeVisible()
  const jobs = await request.get(`/api/v1${prefix}/generation-jobs`, { headers })
  expect(jobs.ok()).toBeTruthy()
  expect((await jobs.json()).data.length).toBeGreaterThan(0)
})
