import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    actionTimeout: 15000,
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:8080',
    viewport: { width: 1440, height: 1000 },
    screenshot: 'off',
    trace: 'off',
    video: 'off',
    locale: 'ru-RU',
    timezoneId: 'Asia/Almaty',
  },
})
