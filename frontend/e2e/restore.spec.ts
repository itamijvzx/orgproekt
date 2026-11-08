import { test, expect, type Page } from '@playwright/test'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { TOTP } from 'otpauth'

test('restored accounts, scoped rights, report bytes and access history survive backup', async ({
  browser,
}) => {
  test.skip(!process.env.RESTORE_LAB, 'Only an explicitly prepared restore lab may run this proof')
  const input = JSON.parse(readFileSync('/private/qa-accounts.json', 'utf8'))
  expect(input.project).toBe(process.env.RESTORE_LAB)
  const errors: string[] = []
  async function login(page: Page, username: string, heading: string) {
    const account = input.accounts[username]
    page.on('pageerror', (e) => errors.push(e.message))
    await page.goto('/')
    await page.getByLabel('Логин', { exact: true }).fill(username)
    await page.getByLabel('Пароль', { exact: true }).fill(account.password)
    await page.getByRole('button', { name: 'Продолжить', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Подтвердите вход', exact: true })).toBeVisible()
    await page
      .getByLabel('Код аутентификатора или резервный код')
      .fill(new TOTP({ secret: account.secret }).generate())
    await page.getByRole('button', { name: 'Подтвердить код', exact: true }).click()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
  }
  const auditorContext = await browser.newContext()
  const auditor = await auditorContext.newPage()
  await login(auditor, 'auditor', 'Рабочий обзор')
  const organization = await (await auditor.request.get('/api/v1/organization')).json()
  expect(organization.departments.length).toBe(5)
  const reportList = await auditor.request.get('/api/v1/reports')
  expect(reportList.status()).toBe(200)
  expect(input.reports.length).toBeGreaterThan(0)
  for (const report of input.reports) {
    for (const format of ['pdf', 'docx']) {
      const file = await auditor.request.get(`/api/v1/reports/${report.id}/export/${format}`)
      expect(file.status()).toBe(200)
      expect(
        createHash('sha256')
          .update(await file.body())
          .digest('hex'),
      ).toBe(report[format])
    }
  }
  await auditor.getByRole('button', { name: 'Изменения', exact: true }).click()
  await auditor.getByRole('menuitem', { name: 'Отчёты', exact: true }).click()
  await expect(auditor.getByRole('heading', { name: 'Отчёты', exact: true })).toBeVisible()
  const selectedReport = (await reportList.json()).items.find(
    (r: { state: string }) => r.state === 'released',
  )
  const reportTitle = selectedReport?.title
  expect(reportTitle).toBeTruthy()
  await auditor.getByRole('button', { name: reportTitle, exact: true }).click()
  await expect(auditor.getByText('Выпущен · Редакция', { exact: false }).first()).toBeVisible()
  await expect(auditor.getByRole('button', { name: 'Скачать PDF', exact: true })).toBeVisible()
  const released = input.reports.find((r: { id: string }) => r.id === selectedReport.id)
  for (const format of ['pdf', 'docx']) {
    const download = auditor.waitForEvent('download')
    await auditor
      .getByRole('button', { name: `Скачать ${format.toUpperCase()}`, exact: true })
      .click()
    const file = await download
    const path = await file.path()
    expect(createHash('sha256').update(readFileSync(path!)).digest('hex')).toBe(released[format])
  }
  mkdirSync('/private/qa', { recursive: true })
  await auditor.screenshot({ path: '/private/qa/report-restored-1440.png', fullPage: true })
  await auditor.reload()
  await expect(auditor.getByRole('heading', { name: 'Рабочий обзор', exact: true })).toBeVisible()

  const itContext = await browser.newContext()
  const it = await itContext.newPage()
  await login(it, 'it', 'Рабочий обзор')
  const itOrg = await (await it.request.get('/api/v1/organization')).json()
  expect(itOrg.departments.map((d: { code: string }) => d.code)).toEqual(['IT'])
  for (const route of ['documents', 'functions', 'overview']) {
    expect(
      (await it.request.get(`/api/v1/${route}?department_id=${input.foreign_department}`)).status(),
    ).toBe(404)
  }
  for (const report of input.reports) {
    expect((await it.request.get(`/api/v1/reports/${report.id}`)).status()).toBe(404)
    expect((await it.request.get(`/api/v1/reports/${report.id}/export/pdf`)).status()).toBe(404)
    expect((await it.request.get(`/api/v1/reports/${report.id}/export/docx`)).status()).toBe(404)
  }
  await it.getByRole('button', { name: 'Организация', exact: true }).click()
  await expect(it.locator('.department-row')).toHaveCount(1)

  const accessContext = await browser.newContext()
  const access = await accessContext.newPage()
  await login(access, 'access', 'Доступ')
  await access.getByRole('tab', { name: 'Пользователи', exact: true }).click()
  await expect(
    access.getByRole('button', { name: 'Пригласить пользователя', exact: true }),
  ).toBeVisible()
  expect((await access.request.get('/api/v1/reports')).status()).toBe(200)
  expect((await (await access.request.get('/api/v1/reports')).json()).total).toBe(0)
  expect((await (await access.request.get('/api/v1/organization')).json()).departments).toEqual([])
  await access.getByRole('tab', { name: 'История доступа', exact: true }).click()
  await expect(access.getByRole('table')).toBeVisible()
  mkdirSync('/private/qa', { recursive: true })
  await access.screenshot({ path: '/private/qa/access-restored-1440.png', fullPage: true })
  await it.screenshot({ path: '/private/qa/it-restored-1440.png', fullPage: true })
  await access.setViewportSize({ width: 390, height: 844 })
  await access.screenshot({ path: '/private/qa/access-restored-390.png', fullPage: true })
  for (const page of [auditor, it, access]) {
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    ).toBeTruthy()
  }
  expect(errors).toEqual([])
  writeFileSync(
    '/private/qa/browser-result.json',
    JSON.stringify({
      result: 'passed',
      roles: ['auditor', 'it', 'access'],
      foreign_scope_denied: true,
      report_bytes_match: true,
      js_errors: errors.length,
    }),
  )
  await Promise.all([auditorContext.close(), itContext.close(), accessContext.close()])
})
