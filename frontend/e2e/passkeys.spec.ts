import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { basename, join } from 'node:path'
import { TOTP } from 'otpauth'

test.use({ channel: 'msedge', ignoreHTTPSErrors: false })
test('register, sign in with local user verification and revoke a platform credential', async ({
  page,
  context,
}, info) => {
  const project = process.env.PASSKEY_QA_PROJECT ?? ''
  const dir = process.env.PASSKEY_QA_DIR ?? ''
  test.skip(!project, 'Disposable Windows HTTPS fixture only')
  expect(project).toMatch(/^orgproekt-passkeys-qa-[0-9a-f]{12}$/)
  expect(basename(dir)).toBe(project)
  expect(process.env.PLAYWRIGHT_BASE_URL).toBe('https://orgproekt-lab.localhost:8444')
  const auth = await context.newCDPSession(page)
  await auth.send('WebAuthn.enable')
  const { authenticatorId } = await auth.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  async function profile() {
    await page.getByRole('button', { name: 'Меню пользователя' }).click()
    await page.getByRole('menuitem', { name: 'Моё пространство' }).click()
  }
  async function logout() {
    await page.getByRole('button', { name: 'Меню пользователя' }).click()
    await page.getByRole('menuitem', { name: 'Выйти' }).click()
    await expect(page.getByRole('heading', { name: 'Войти в OrgProekt' })).toBeVisible()
  }
  const password = JSON.parse(readFileSync(join(dir, 'accounts', 'demo-accounts.json'), 'utf8'))
    .accounts.auditor.password
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Войти по ключу доступа' })).toBeVisible()
  await page.getByLabel('Логин', { exact: true }).fill('auditor')
  await page.getByLabel('Пароль', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click()
  const secret = await page.getByTestId('mfa-secret').textContent()
  await page
    .getByLabel('Код аутентификатора', { exact: true })
    .fill(new TOTP({ secret: secret! }).generate())
  await page.getByRole('button', { name: 'Подтвердить код' }).click()
  await page.getByLabel('Я сохранил(а) резервные коды').check()
  await page.getByRole('button', { name: 'Открыть рабочее пространство' }).click()
  await profile()
  await page.getByRole('button', { name: 'Добавить ключ Windows Hello' }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('passkey-setup-1440.png') })
  await page.getByLabel('Название ключа').fill('Учебный компьютер QA')
  await page.getByRole('button', { name: 'Добавить ключ Windows Hello' }).click()
  await expect(page.getByText('Учебный компьютер QA', { exact: true })).toBeVisible()
  await page.reload()
  // Navigation is reset by refresh on some clients.
  if (!(await page.getByRole('heading', { name: 'Ключи доступа' }).isVisible())) await profile()
  await expect(page.getByText('Учебный компьютер QA', { exact: true })).toBeVisible()
  await logout()
  await page.getByRole('button', { name: 'Войти по ключу доступа' }).click()
  await expect(page.getByRole('heading', { name: 'Рабочий обзор', exact: true })).toBeVisible()
  expect(
    await page.evaluate(async () => (await (await fetch('/api/v1/auth/me')).json()).username),
  ).toBe('auditor')
  await profile()
  await page.getByRole('heading', { name: 'Ключи доступа' }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: info.outputPath('passkey-settings-1440.png') })
  await page.getByRole('button', { name: 'Отозвать', exact: true }).click()
  await page.getByRole('button', { name: 'Подтвердить отзыв' }).click()
  await expect(page.getByText('Подключённых ключей пока нет.')).toBeVisible()
  await logout()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: info.outputPath('passkey-login-390.png') })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'Войти по ключу доступа' }).click()
  await expect(page.getByRole('alert')).toContainText('Не удалось подтвердить ключ')
  expect(await page.evaluate(async () => (await fetch('/api/v1/auth/me')).status)).toBe(401)
  expect(errors).toEqual([])
  await auth.send('WebAuthn.removeVirtualAuthenticator', { authenticatorId })
})
