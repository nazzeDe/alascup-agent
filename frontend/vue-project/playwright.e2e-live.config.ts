import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e-live',
  timeout: 60000,
  expect: { timeout: 15000 },
  retries: 0,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    headless: !process.env.E2E_LIVE_HEADED,
    viewport: { width: 1280, height: 800 },
    actionTimeout: 10000,
  },
})
