import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { signIn } from './report-helpers'

test('Учебный режим: вход без телефона, повторный пароль, сохранение и мобильный экран', async ({
  page,
}) => {
  test.skip(process.env.ORGPROEKT_DEMO_E2E !== '1', 'Explicit local demo stack only')
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const credentials = JSON.parse(readFileSync('/private/demo-accounts.json', 'utf8')).accounts
  await page.goto('/')
  await expect(
    page.getByText('Локальный учебный режим · MFA отключена', { exact: true }),
  ).toBeVisible()
  await page.screenshot({ path: '/screenshots/61-demo-login-1440.png', fullPage: true })
  await signIn(page, 'auditor')
  await page.getByRole('button', { name: 'Меню пользователя', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Моё пространство', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Безопасность входа', exact: true })).toBeVisible()
  await expect(page.getByLabel('Новый код MFA или резервный код')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Добавить ключ Windows Hello/ })).toHaveCount(0)
  await page.getByLabel('Текущий пароль', { exact: true }).fill('incorrect-demo-password')
  await page.getByRole('button', { name: 'Подтвердить вход', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Не удалось подтвердить вход')
  await page.getByLabel('Текущий пароль', { exact: true }).fill(credentials.auditor.password)
  await page.getByRole('button', { name: 'Подтвердить вход', exact: true }).click()
  await expect(page.getByText('Вход подтверждён на следующие 10 минут')).toBeVisible()
  await expect(page.getByText('Текущая сессия', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Рабочий обзор', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Меню пользователя', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Моё пространство', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Безопасность входа', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
  await page.screenshot({ path: '/screenshots/62-demo-settings-390.png', fullPage: true })
  expect(errors).toEqual([])
})
