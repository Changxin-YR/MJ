import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  use: { baseURL: 'http://localhost:28080', trace: 'retain-on-failure', screenshot: 'only-on-failure', ...devices['Desktop Chrome'] },
})
