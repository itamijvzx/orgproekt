import { expect, type Page } from '@playwright/test'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { TOTP } from 'otpauth'
const credentials = JSON.parse(readFileSync('/private/demo-accounts.json', 'utf8')).accounts
const factorsPath = '/private/test-factors.json'
export async function signIn(
  page: Page,
  user: string,
  options: { password?: string; heading?: string } = {},
) {
  const factors: Record<string, { secret: string; codes: string[] }> = existsSync(factorsPath)
    ? JSON.parse(readFileSync(factorsPath, 'utf8'))
    : {}
  await page.goto('/')
  await page.getByLabel('Логин', { exact: true }).fill(user)
  await page
    .getByLabel('Пароль', { exact: true })
    .fill(options.password ?? credentials[user].password)
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click()
  if (process.env.ORGPROEKT_DEMO_E2E === '1') {
    expect((await (await page.request.get('/api/v1/auth/policy')).json()).local_demo).toBe(true)
    await expect(
      page.getByRole('heading', { name: options.heading ?? 'Рабочий обзор', exact: true }),
    ).toBeVisible()
    await expect(page.getByText(/Локальный учебный режим · MFA отключена/)).toBeVisible()
    await expect(page.getByLabel('Код аутентификатора', { exact: true })).toHaveCount(0)
    return
  }
  expect((await (await page.request.get('/api/v1/auth/policy')).json()).local_demo).toBe(false)
  if (!factors[user]) {
    await expect(page.getByRole('heading', { name: 'Защитите свой вход' })).toBeVisible()
    const secret = await page.getByTestId('mfa-secret').textContent()
    await page
      .getByLabel('Код аутентификатора', { exact: true })
      .fill(new TOTP({ secret: secret! }).generate())
    await page.getByRole('button', { name: 'Подтвердить код' }).click()
    await expect(page.getByRole('heading', { name: 'Сохраните резервные коды' })).toBeVisible()
    factors[user] = {
      secret: secret!,
      codes: await page.locator('.recovery-grid code').allTextContents(),
    }
    writeFileSync(factorsPath, JSON.stringify(factors), { mode: 0o600 })
    await page.getByLabel('Я сохранил(а) резервные коды').check()
    await page.getByRole('button', { name: 'Открыть рабочее пространство' }).click()
  } else {
    const code = factors[user].codes.shift()!
    writeFileSync(factorsPath, JSON.stringify(factors), { mode: 0o600 })
    await page.getByLabel('Код аутентификатора или резервный код').fill(code)
    await page.getByRole('button', { name: 'Подтвердить код' }).click()
  }
  await expect(
    page.getByRole('heading', { name: options.heading ?? 'Рабочий обзор', exact: true }),
  ).toBeVisible()
}
