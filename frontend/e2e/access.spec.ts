import { test, expect, type Page } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { signIn } from './report-helpers'

async function shot(page: Page, name: string) {
  await expect
    .poll(() => page.locator('.page-content').evaluate((el) => getComputedStyle(el).opacity))
    .toBe('1')
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: '/screenshots/' + name, fullPage: true, animations: 'disabled' })
}
async function command(page: Page, path: string, action: () => Promise<unknown>) {
  const pending = page.waitForResponse(
    (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === path,
  )
  await action()
  const response = await pending
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}

test('Этап 4: приглашение → MFA → подтверждение прав → изоляция → отзыв → блокировка', async ({
  page,
  browser,
}) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const username = 'access_e2e_' + Date.now(),
    display = 'Елена Береговая · проверка доступа'
  const password = randomBytes(24).toString('base64url')
  await signIn(page, 'access', { heading: 'Доступ' })
  await expect(
    page.getByRole('heading', { name: 'Пользователи и права', exact: true }),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Организация', exact: true })).toHaveCount(0)
  const options = await (await page.request.get('/api/v1/access/options')).json()
  const finance = options.departments.find(
    (d: { label: string }) => d.label === 'Финансы и планирование',
  )
  expect((await (await page.request.get('/api/v1/organization')).json()).departments).toEqual([])
  await shot(page, '51-access-users-1440.png')
  await page.getByRole('button', { name: 'Пригласить пользователя', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Логин нового пользователя', { exact: true }).fill(username)
  await dialog.getByLabel('Имя пользователя', { exact: true }).fill(display)
  await dialog
    .getByLabel('Основание приглашения', { exact: true })
    .fill('Учебное приглашение представителя ИТ для проверки жизненного цикла доступа.')
  const invitation = await command(page, '/api/v1/access/invitations', () =>
    dialog.getByRole('button', { name: 'Создать приглашение', exact: true }).click(),
  )
  await expect(page.getByRole('tab', { name: 'Приглашения', exact: true })).toHaveAttribute(
    'data-state',
    'active',
  )
  const invitationRow = page.getByRole('row').filter({ hasText: username })
  await invitationRow.getByRole('button', { name: 'Получить код приглашения', exact: true }).click()
  const delivery = page.getByRole('dialog')
  await expect(
    delivery.getByRole('heading', { name: 'Учебная доставка приглашения', exact: true }),
  ).toBeVisible()
  const token = await delivery.getByLabel('Код приглашения', { exact: true }).inputValue()
  expect(token.length).toBeGreaterThanOrEqual(32)
  await delivery.getByRole('button', { name: 'Закрыть код', exact: true }).click()

  const personContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Asia/Almaty',
  })
  const person = await personContext.newPage()
  person.on('pageerror', (e) => errors.push(e.message))
  await person.goto('/')
  await person.getByRole('button', { name: 'У меня есть приглашение', exact: true }).click()
  await person.getByLabel('Код приглашения', { exact: true }).fill(token)
  await person.getByLabel('Новый пароль · от 14 символов', { exact: true }).fill(password)
  await command(person, '/api/v1/auth/accept-invitation', () =>
    person.getByRole('button', { name: 'Создать личную учётную запись', exact: true }).click(),
  )
  await expect(
    person.getByRole('heading', { name: 'Войти в OrgProekt', exact: true }),
  ).toBeVisible()
  expect(
    (
      await person.request.post('/api/v1/auth/accept-invitation', {
        data: { token, password },
        headers: { Origin: new URL(person.url()).origin },
      })
    ).status(),
  ).toBe(400)
  await signIn(person, username, { password, heading: 'Моё пространство' })
  await expect(
    person.getByRole('status').filter({ hasText: 'Права на подразделение ещё не назначены' }),
  ).toBeVisible()
  expect((await (await person.request.get('/api/v1/auth/me')).json()).roles).toEqual([])

  await page.getByRole('tab', { name: 'Пользователи', exact: true }).click()
  await page.getByLabel('Поиск пользователей', { exact: true }).fill(username)
  await page.getByRole('button', { name: display, exact: true }).click()
  await page.getByRole('button', { name: 'Запросить назначение роли', exact: true }).click()
  await dialog.getByRole('combobox', { name: 'Роль', exact: true }).selectOption('department')
  await dialog
    .getByRole('combobox', { name: 'Область доступа', exact: true })
    .selectOption({ label: 'Информационные технологии' })
  await dialog
    .getByLabel('Основание назначения', { exact: true })
    .fill(
      'Учебный доступ только к ИТ на ограниченный срок. Документальные сведения финансового отдела не предоставляются.',
    )
  const requested = await command(page, '/api/v1/access/requests', () =>
    dialog.getByRole('button', { name: 'Направить на подтверждение', exact: true }).click(),
  )
  await expect(page.getByRole('status').filter({ hasText: 'Запрос отправлен' })).toBeVisible()
  expect((await (await person.request.get('/api/v1/organization')).json()).departments).toEqual([])
  await page.getByRole('tab', { name: 'Назначения на подтверждении', exact: true }).click()
  await shot(page, '52-access-request-1440.png')

  const managerContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Asia/Almaty',
  })
  const manager = await managerContext.newPage()
  manager.on('pageerror', (e) => errors.push(e.message))
  await signIn(manager, 'stage3_manager')
  await manager.getByRole('button', { name: 'Ещё', exact: true }).click()
  await manager.getByRole('menuitem', { name: 'Доступ', exact: true }).click()
  await manager.getByRole('button', { name: display, exact: true }).click()
  const review = manager.getByRole('dialog')
  await review
    .getByLabel('Обоснование решения', { exact: true })
    .fill(
      'Личность учебного получателя, роль представителя ИТ, область и срок проверены. Назначение подтверждено.',
    )
  await review.getByRole('checkbox', { name: /Проверены личность получателя/ }).check()
  await shot(manager, '53-access-manager-review-1440.png')
  await command(manager, `/api/v1/access/requests/${requested.id}/decision`, () =>
    review.getByRole('button', { name: 'Сохранить решение по правам', exact: true }).click(),
  )
  await expect(review).not.toBeVisible()
  await person.reload()
  await expect(
    person.getByRole('heading', { name: 'Войти в OrgProekt', exact: true }),
  ).toBeVisible()
  await signIn(person, username, { password })
  const organization = await (await person.request.get('/api/v1/organization')).json()
  expect(organization.departments.map((d: { code: string }) => d.code)).toEqual(['IT'])
  for (const path of [
    '/documents?department_id=' + finance.id,
    '/functions?department_id=' + finance.id,
    '/overview?department_id=' + finance.id,
  ])
    expect((await person.request.get('/api/v1' + path)).status()).toBe(404)
  expect((await person.request.get('/api/v1/access/users')).status()).toBe(404)
  await person.getByRole('button', { name: 'Организация', exact: true }).click()
  await expect(person.locator('.department-row')).toHaveCount(1)
  await shot(person, '54-access-scoped-user-1440.png')

  await page.getByRole('tab', { name: 'Пользователи', exact: true }).click()
  await page.getByLabel('Поиск пользователей', { exact: true }).fill(username)
  await page.getByRole('button', { name: display, exact: true }).click()
  await expect(
    page.getByRole('button', { name: /Отозвать Представитель отдела Информационные технологии/ }),
  ).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await shot(page, '55-access-user-rights-390.png')
  const revokeBounds = await page
    .getByRole('button', { name: /Отозвать Представитель отдела Информационные технологии/ })
    .boundingBox()
  expect(revokeBounds!.x).toBeGreaterThanOrEqual(0)
  expect(revokeBounds!.x + revokeBounds!.width).toBeLessThanOrEqual(390)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page
    .getByRole('button', { name: /Отозвать Представитель отдела Информационные технологии/ })
    .click()
  await page
    .getByLabel('Основание действия', { exact: true })
    .fill('Учебный доступ завершён. Назначение отзывается с сохранением истории.')
  const details = await (await page.request.get('/api/v1/access/users/' + requested.user_id)).json()
  const grant = details.grants.find((g: { state: string }) => g.state === 'active')
  await command(page, `/api/v1/access/grants/${grant.id}/revoke`, () =>
    page.getByRole('button', { name: 'Подтвердить действие', exact: true }).click(),
  )
  await expect(page.getByRole('cell', { name: 'Отозвано', exact: false })).toBeVisible()
  expect((await person.request.get('/api/v1/auth/me')).status()).toBe(401)
  await signIn(person, username, { password, heading: 'Моё пространство' })
  expect((await (await person.request.get('/api/v1/organization')).json()).departments).toEqual([])
  await page.getByRole('button', { name: 'Заблокировать аккаунт', exact: true }).click()
  await page
    .getByLabel('Основание действия', { exact: true })
    .fill('Учебная проверка блокировки без сброса пароля и MFA.')
  await command(page, `/api/v1/access/users/${requested.user_id}/block`, () =>
    page.getByRole('button', { name: 'Подтвердить действие', exact: true }).click(),
  )
  await expect(
    page.getByRole('button', { name: 'Разблокировать аккаунт', exact: true }),
  ).toBeVisible()
  expect(
    (
      await person.request.post('/api/v1/auth/login', {
        data: { username, password },
        headers: { Origin: new URL(person.url()).origin },
      })
    ).status(),
  ).toBe(401)
  await page.getByRole('button', { name: 'Разблокировать аккаунт', exact: true }).click()
  await page
    .getByLabel('Основание действия', { exact: true })
    .fill('Учебная проверка личности завершена. Существующие пароль и MFA сохраняются.')
  await command(page, `/api/v1/access/users/${requested.user_id}/unblock`, () =>
    page.getByRole('button', { name: 'Подтвердить действие', exact: true }).click(),
  )
  await expect(
    page.getByRole('button', { name: 'Заблокировать аккаунт', exact: true }),
  ).toBeVisible()
  await signIn(person, username, { password, heading: 'Моё пространство' })
  await page.getByRole('button', { name: 'Завершить все сессии', exact: true }).click()
  await page
    .getByLabel('Основание действия', { exact: true })
    .fill('Учебная проверка административного завершения активной сессии.')
  await command(page, `/api/v1/access/users/${requested.user_id}/revoke-sessions`, () =>
    page.getByRole('button', { name: 'Подтвердить действие', exact: true }).click(),
  )
  expect((await person.request.get('/api/v1/auth/me')).status()).toBe(401)
  await page.getByRole('tab', { name: 'История доступа', exact: true }).click()
  await expect(
    page.getByRole('cell', { name: 'Назначение роли отозвано', exact: true }).first(),
  ).toBeVisible()
  await shot(page, '56-access-history-1440.png')
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Доступ', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'Приглашения', exact: true }).click()
  const completed = page.getByRole('row').filter({ hasText: username })
  await expect(completed.getByRole('cell', { name: /Активировано/ })).toBeVisible()
  expect(
    (
      await page.request.post(`/api/v1/access/invitations/${invitation.id}/delivery`, {
        headers: {
          Origin: new URL(page.url()).origin,
          'X-CSRF-Token': (await (await page.request.get('/api/v1/auth/me')).json()).csrf_token,
        },
      })
    ).status(),
  ).toBe(409)
  expect(errors).toEqual([])
  await personContext.close()
  await managerContext.close()
})
