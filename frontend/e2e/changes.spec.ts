import { test, expect, type Page } from '@playwright/test'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { TOTP } from 'otpauth'

const credentials = JSON.parse(readFileSync('/private/demo-accounts.json', 'utf8')).accounts
const factorsPath = '/private/test-factors.json'
const title = `Уточнение порядка восстановления · E2E этап 3 · ${Date.now()}`

async function signIn(page: Page, user: string) {
  const factors: Record<string, { secret: string; codes: string[] }> = existsSync(factorsPath)
    ? JSON.parse(readFileSync(factorsPath, 'utf8'))
    : {}
  await page.goto('/')
  await page.getByLabel('Логин', { exact: true }).fill(user)
  await page.getByLabel('Пароль', { exact: true }).fill(credentials[user].password)
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click()
  if (process.env.ORGPROEKT_DEMO_E2E === '1') {
    expect((await (await page.request.get('/api/v1/auth/policy')).json()).local_demo).toBe(true)
    await expect(page.getByRole('heading', { name: 'Рабочий обзор', exact: true })).toBeVisible()
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
  await expect(page.getByRole('heading', { name: 'Рабочий обзор', exact: true })).toBeVisible()
}

async function openChanges(page: Page) {
  const mobile = page.getByRole('button', { name: 'Открыть меню', exact: true })
  if (await mobile.isVisible()) {
    await mobile.click()
    await page.getByRole('dialog').getByRole('button', { name: 'Предложения', exact: true }).click()
  } else {
    await page.getByRole('button', { name: 'Изменения', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Предложения', exact: true }).click()
  }
}

async function openProposal(page: Page) {
  await openChanges(page)
  await page.getByRole('button', { name: title, exact: true }).click()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
}

async function capture(page: Page, file: string) {
  await expect
    .poll(() => page.locator('.page-content').evaluate((el) => getComputedStyle(el).opacity))
    .toBe('1')
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: '/screenshots/' + file, fullPage: true, animations: 'disabled' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
}

async function command(page: Page, path: string, click: () => Promise<unknown>) {
  const pending = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === path,
    { timeout: 15_000 },
  )
  await click()
  const response = await pending
  expect(response.status(), response.ok() ? undefined : await response.text()).toBe(200)
}

test('Этап 3: черновик → личная заметка → отправка → обсуждение → согласование → решение', async ({
  browser,
  page,
}) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await signIn(page, 'auditor')
  await openChanges(page)
  await page.getByRole('button', { name: 'Новое предложение', exact: true }).click()
  await page.getByLabel('Название предложения', { exact: true }).fill(title)
  await page.getByLabel('Проектная дата', { exact: true }).fill('2026-10-29')
  await page
    .getByRole('combobox', { name: 'Подразделение 1', exact: true })
    .selectOption({ label: 'Информационные технологии' })
  await page
    .getByLabel('Название раздела 1', { exact: true })
    .fill('Порядок документирования восстановления')
  await page
    .getByRole('textbox', { name: 'Цель и обоснование 1', exact: true })
    .fill('Уточнить форму фиксации проверки резервных копий. Действующая структура сохраняется.')
  await page
    .getByRole('combobox', { name: 'Вид изменения 1.1', exact: true })
    .selectOption('document')
  await page
    .getByRole('textbox', { name: 'До изменения 1.1', exact: true })
    .fill('Результаты проверки фиксируются в рабочем журнале.')
  await page
    .getByRole('textbox', { name: 'После изменения 1.1', exact: true })
    .fill(
      'Результаты проверки фиксируются в журнале со ссылкой на проверенный комплект резервных копий.',
    )
  const sources = page.getByRole('combobox', { name: 'Основания изменения 1.1', exact: true })
  await expect.poll(() => sources.locator('option').count()).toBeGreaterThan(1)
  const seedSource = await sources.locator('option').evaluateAll(
    (options) =>
      options
        .map((option) => ({
          value: (option as HTMLOptionElement).value,
          label: option.textContent ?? '',
        }))
        .find((option) => option.value && option.label.includes('Инструкция'))?.value,
  )
  expect(seedSource).toBeTruthy()
  await sources.selectOption(seedSource!)
  await page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  const listed = await page.request.get('/api/v1/changes?q=' + encodeURIComponent(title))
  expect(listed.ok()).toBe(true)
  const proposalId = (await listed.json()).items[0].id
  const before = await (await page.request.get('/api/v1/organization?as_of=2026-10-29')).json()
  await page.getByRole('tab', { name: 'Личные заметки', exact: true }).click()
  const notesPanel = page.getByRole('tabpanel', { name: 'Личные заметки', exact: true })
  await notesPanel
    .getByRole('textbox', { name: 'Текст личной заметки', exact: true })
    .fill('ЛИЧНОЕ_E2E_753: проверить полноту документальных оснований до решения.')
  await command(page, `/api/v1/changes/${proposalId}/notes`, () =>
    notesPanel.getByRole('button', { name: 'Сохранить личную заметку', exact: true }).click(),
  )
  await expect(
    notesPanel
      .locator('.change-message')
      .getByText('ЛИЧНОЕ_E2E_753: проверить полноту документальных оснований до решения.', {
        exact: true,
      }),
  ).toBeVisible()
  await page.getByRole('tab', { name: 'Предложение', exact: true }).click()
  await capture(page, '31-change-draft-1440.png')
  await page.getByRole('button', { name: 'Направить на согласование', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('checkbox', { name: /Лев Соснов/ }).check()
  await dialog.getByRole('checkbox', { name: /Марк Кедров/ }).check()
  await dialog.getByRole('checkbox', { name: /София Учебная/ }).check()
  await dialog.getByRole('checkbox', { name: /Я проверил\(а\) версию/ }).check()
  await command(page, `/api/v1/changes/${proposalId}/send`, () =>
    dialog.getByRole('button', { name: 'Отправить версию участникам', exact: true }).click(),
  )
  await expect(dialog).not.toBeVisible()

  const departmentContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Asia/Almaty',
  })
  const department = await departmentContext.newPage()
  department.on('pageerror', (e) => errors.push(e.message))
  await signIn(department, 'it')
  await openProposal(department)
  await expect(department.getByRole('tab', { name: 'Личные заметки', exact: true })).toHaveCount(0)
  const scoped = await department.request.get(`/api/v1/changes/${proposalId}`)
  expect(scoped.ok()).toBe(true)
  expect(await scoped.text()).not.toContain('ЛИЧНОЕ_E2E_753')
  await department.getByRole('tab', { name: 'Обсуждение', exact: true }).click()
  const discussionPanel = department.getByRole('tabpanel', { name: 'Обсуждение', exact: true })
  await discussionPanel
    .getByRole('textbox', { name: 'Текст сообщения', exact: true })
    .fill('Подразделение подтвердило необходимость уточнения журнала.')
  await command(department, `/api/v1/changes/${proposalId}/messages`, () =>
    discussionPanel.getByRole('button', { name: 'Отправить сообщение', exact: true }).click(),
  )
  await expect(
    discussionPanel
      .locator('.change-message')
      .getByText('Подразделение подтвердило необходимость уточнения журнала.', {
        exact: true,
      }),
  ).toBeVisible()
  await department.getByRole('tab', { name: 'Согласование', exact: true }).click()
  const departmentApproval = department.getByRole('tabpanel', { name: 'Согласование', exact: true })
  await departmentApproval
    .getByRole('combobox', { name: 'Результат рассмотрения', exact: true })
    .selectOption('agree')
  const reviewField = departmentApproval.getByRole('textbox', {
    name: 'Обоснование ответа',
    exact: true,
  })
  await reviewField.fill('Согласовано отделом ИТ: полномочия и формулировка проверены.')
  await expect(reviewField).toHaveValue(
    'Согласовано отделом ИТ: полномочия и формулировка проверены.',
  )
  const departmentReviewResponse = department.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/changes/${proposalId}/reviews`,
    { timeout: 15_000 },
  )
  await departmentApproval
    .getByRole('button', { name: 'Отправить ответ подразделения', exact: true })
    .click()
  const departmentReview = await departmentReviewResponse
  expect(
    departmentReview.status(),
    departmentReview.ok() ? undefined : await departmentReview.text(),
  ).toBe(200)
  await expect(
    departmentApproval
      .locator('.change-message')
      .getByText('Согласовано отделом ИТ: полномочия и формулировка проверены.', {
        exact: true,
      }),
  ).toBeVisible()
  await expect(
    departmentApproval.getByRole('combobox', { name: 'Управленческое решение', exact: true }),
  ).toHaveCount(0)
  await capture(department, '32-change-department-review-1440.png')
  await department.setViewportSize({ width: 390, height: 844 })
  await capture(department, '33-change-department-review-390.png')

  const hrContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Asia/Almaty',
  })
  const hr = await hrContext.newPage()
  await signIn(hr, 'hr')
  await openProposal(hr)
  await hr.getByRole('tab', { name: 'Согласование', exact: true }).click()
  const hrApproval = hr.getByRole('tabpanel', { name: 'Согласование', exact: true })
  await hrApproval
    .getByRole('combobox', { name: 'Результат рассмотрения', exact: true })
    .selectOption('agree')
  await hrApproval
    .getByRole('textbox', { name: 'Обоснование ответа', exact: true })
    .fill('HR проверил документальные основания и согласовал версию 1.')
  await expect(
    hrApproval.getByRole('textbox', { name: 'Обоснование ответа', exact: true }),
  ).toHaveValue('HR проверил документальные основания и согласовал версию 1.')
  const hrReviewResponse = hr.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new URL(response.url()).pathname === `/api/v1/changes/${proposalId}/reviews`,
    { timeout: 15_000 },
  )
  await hrApproval
    .getByRole('button', { name: 'Отправить ответ подразделения', exact: true })
    .click()
  const hrReview = await hrReviewResponse
  expect(hrReview.status(), hrReview.ok() ? undefined : await hrReview.text()).toBe(200)
  await expect(
    hrApproval
      .locator('.change-message')
      .getByText('HR проверил документальные основания и согласовал версию 1.', { exact: true }),
  ).toBeVisible()

  const managerContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
    timezoneId: 'Asia/Almaty',
  })
  const manager = await managerContext.newPage()
  await signIn(manager, 'stage3_manager')
  await openProposal(manager)
  await manager.getByRole('tab', { name: 'Согласование', exact: true }).click()
  const managerApproval = manager.getByRole('tabpanel', { name: 'Согласование', exact: true })
  await managerApproval
    .getByRole('combobox', { name: 'Управленческое решение', exact: true })
    .selectOption('approve')
  await managerApproval
    .getByRole('textbox', { name: 'Основание решения', exact: true })
    .fill('Утверждаю версию 1 после ответа уполномоченного подразделения.')
  await expect
    .poll(() =>
      managerApproval
        .getByRole('combobox', { name: 'Документы к решению', exact: true })
        .locator('option')
        .count(),
    )
    .toBeGreaterThan(1)
  await managerApproval
    .getByRole('combobox', { name: 'Документы к решению', exact: true })
    .selectOption({ index: 1 })
  await command(manager, `/api/v1/changes/${proposalId}/decisions`, () =>
    managerApproval
      .getByRole('button', { name: 'Зафиксировать решение руководителя', exact: true })
      .click(),
  )
  await expect(
    managerApproval
      .locator('.change-message')
      .getByText('Утверждаю версию 1 после ответа уполномоченного подразделения.', {
        exact: true,
      }),
  ).toBeVisible()
  await capture(manager, '34-change-manager-approved-1440.png')
  await page.reload()
  await openProposal(page)
  const persisted = await (await page.request.get(`/api/v1/changes/${proposalId}`)).json()
  expect(persisted.state).toBe('approved')
  expect(persisted.entries.some((entry: { kind: string }) => entry.kind === 'execution')).toBe(
    false,
  )
  expect(await (await page.request.get('/api/v1/organization?as_of=2026-10-29')).json()).toEqual(
    before,
  )
  await hr.reload()
  await openProposal(hr)
  await hr.getByRole('tab', { name: 'Исполнение', exact: true }).click()
  const hrExecution = hr.getByRole('tabpanel', { name: 'Исполнение', exact: true })
  await hrExecution
    .getByRole('combobox', { name: 'Состояние исполнения', exact: true })
    .selectOption('completed')
  await hrExecution
    .getByRole('textbox', { name: 'Что выполнено или запланировано', exact: true })
    .fill('HR подтвердил документальное исполнение пункта по указанному основанию.')
  const executionSource = hrExecution.getByRole('combobox', {
    name: 'Документ-основание исполнения',
    exact: true,
  })
  await expect.poll(() => executionSource.locator('option').count()).toBeGreaterThan(1)
  // Choose the same seed source used for the proposed change, not a later uploaded draft.
  await executionSource.selectOption(
    persisted.versions[0].sections[0].items[0].source_anchor_ids[0],
  )
  await command(hr, `/api/v1/changes/${proposalId}/execution`, () =>
    hrExecution.getByRole('button', { name: 'Зарегистрировать исполнение', exact: true }).click(),
  )
  await expect(
    hrExecution
      .locator('.change-message')
      .getByText('HR подтвердил документальное исполнение пункта по указанному основанию.', {
        exact: true,
      }),
  ).toBeVisible()
  await capture(hr, '35-change-hr-executed-1440.png')
  await page.reload()
  await openProposal(page)
  await page.getByRole('tab', { name: 'Исполнение', exact: true }).click()
  const auditorExecution = page.getByRole('tabpanel', { name: 'Исполнение', exact: true })
  await auditorExecution
    .getByRole('textbox', { name: 'Заключение о проверке исполнения', exact: true })
    .fill('Аудитор проверил сохраненные основания и полноту исполнения предложения.')
  await command(page, `/api/v1/changes/${proposalId}/verify`, () =>
    auditorExecution
      .getByRole('button', { name: 'Подтвердить проверку исполнения', exact: true })
      .click(),
  )
  await expect
    .poll(
      async () => (await (await page.request.get(`/api/v1/changes/${proposalId}`)).json()).state,
    )
    .toBe('verified')
  await expect(
    auditorExecution
      .locator('.change-message')
      .getByText('Аудитор проверил сохраненные основания и полноту исполнения предложения.', {
        exact: true,
      }),
  ).toBeVisible()
  await capture(page, '36-change-auditor-verified-1440.png')
  expect(await (await page.request.get('/api/v1/organization?as_of=2026-10-29')).json()).toEqual(
    before,
  )
  await page.getByRole('tab', { name: 'История', exact: true }).click()
  await expect(
    page
      .getByRole('tabpanel', { name: 'История', exact: true })
      .locator('.change-history')
      .getByText(
        'Утверждено руководителем. Утверждаю версию 1 после ответа уполномоченного подразделения.',
        {
          exact: true,
        },
      ),
  ).toBeVisible()

  const foreignContext = await browser.newContext()
  const finance = await foreignContext.newPage()
  await signIn(finance, 'finance')
  expect((await finance.request.get(`/api/v1/changes/${proposalId}`)).status()).toBe(404)
  const foreignList = await (
    await finance.request.get('/api/v1/changes?q=' + encodeURIComponent(title))
  ).json()
  expect(foreignList.total).toBe(0)
  expect(errors).toEqual([])
  await foreignContext.close()
  await hrContext.close()
  await managerContext.close()
  await departmentContext.close()
})
