import { expect, test } from '@playwright/test'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { TOTP } from 'otpauth'
import { signIn } from './report-helpers'

test('invalid search input stays a safe error and department access remains scoped', async ({ page }) => {
  await signIn(page, 'it')
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.getByRole('navigation', { name: 'Основная навигация' })
    .getByRole('button', { name: 'Документы', exact: true }).click()
  await page.getByLabel('Поиск документов', { exact: true }).fill('private-probe\u0000text')
  await expect(page.getByText('Текстовые поля не могут содержать нулевой символ', { exact: true })).toBeVisible()
  await page.screenshot({ path: '/screenshots/security-invalid-search.png', fullPage: true })
  await page.getByLabel('Поиск документов', { exact: true }).fill('')
  await expect(page.getByText('Текстовые поля не могут содержать нулевой символ', { exact: true })).toHaveCount(0)
  await page.reload()
  expect((await page.request.get('/api/v1/documents')).status()).toBe(200)
  expect((await page.request.get('/api/v1/access/users')).status()).toBe(404)
  expect(errors).toEqual([])
  await page.screenshot({ path: '/screenshots/security-search.png', fullPage: true })
})

test('MFA rejects Unicode before accepting a valid code', async ({ page }) => {
  test.skip(process.env.ORGPROEKT_DEMO_E2E === '1', 'MFA deliberately disabled in local demo')
  const credentials = JSON.parse(readFileSync('/private/demo-accounts.json', 'utf8')).accounts
  await page.goto('/')
  await page.getByLabel('Логин', { exact: true }).fill('finance')
  await page.getByLabel('Пароль', { exact: true }).fill(credentials.finance.password)
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Подтвердить код', exact: true })).toBeVisible()
  const state = await (await page.request.get('/api/v1/auth/state')).json()
  const rejected = await page.request.post(state.enrollment_required ? '/api/v1/auth/mfa/confirm' : '/api/v1/auth/mfa/verify', {
    headers: { 'X-CSRF-Token': state.csrf_token, Origin: new URL(page.url()).origin },
    data: { code: '１２３４５６' },
  })
  expect(rejected.status()).toBe(422)
  expect((await page.request.get('/api/v1/auth/me')).status()).toBe(403)
  if (state.enrollment_required) {
    await expect(page.getByRole('heading', { name: 'Защитите свой вход' })).toBeVisible()
    const secret = await page.getByTestId('mfa-secret').textContent()
    await page.getByLabel('Код аутентификатора', { exact: true }).fill(new TOTP({ secret: secret! }).generate())
    await page.getByRole('button', { name: 'Подтвердить код', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Сохраните резервные коды' })).toBeVisible()
    const file = '/private/test-factors.json'
    const factors = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : {}
    factors.finance = { secret, codes: await page.locator('.recovery-grid code').allTextContents() }
    writeFileSync(file, JSON.stringify(factors), { mode: 0o600 })
    await page.getByLabel('Я сохранил(а) резервные коды').check()
    await page.getByRole('button', { name: 'Открыть рабочее пространство' }).click()
  } else {
    const file = '/private/test-factors.json'
    const factors = JSON.parse(readFileSync(file, 'utf8'))
    await page.getByLabel('Код аутентификатора или резервный код').fill(factors.finance.codes.shift())
    writeFileSync(file, JSON.stringify(factors), { mode: 0o600 })
    await page.getByRole('button', { name: 'Подтвердить код', exact: true }).click()
  }
  await expect(page.getByRole('heading', { name: 'Рабочий обзор', exact: true })).toBeVisible()
})
