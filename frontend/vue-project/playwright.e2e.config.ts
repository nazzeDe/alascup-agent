import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  expect: { timeout: 10000 },
  retries: 0,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5174',
    headless: true,
    viewport: { width: 1280, height: 800 },
    actionTimeout: 10000,
  },
  webServer: process.env.E2E_SKIP_WEB_SERVER
    ? undefined
    : {
        command: 'bun run dev --port 5174',
        port: 5174,
        reuseExistingServer: true,
        timeout: 30000,
      },
})
