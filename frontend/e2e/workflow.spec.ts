import { test, expect, type Page } from '@playwright/test'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { TOTP } from 'otpauth'
import { createHash } from 'node:crypto'

const day = '2026-10-28'
const credentials = JSON.parse(readFileSync('/private/demo-accounts.json', 'utf8')).accounts
const factorsPath = '/private/test-factors.json'
const factors: Record<string, { secret: string; codes: string[] }> = existsSync(factorsPath)
  ? JSON.parse(readFileSync(factorsPath, 'utf8'))
  : {}

async function signIn(page: Page, user: string) {
  Object.assign(
    factors,
    existsSync(factorsPath) ? JSON.parse(readFileSync(factorsPath, 'utf8')) : {},
  )
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
    const codes = await page.locator('.recovery-grid code').allTextContents()
    factors[user] = { secret: secret!, codes }
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

async function navigate(page: Page, label: string) {
  const mobileMenu = page.getByRole('button', { name: 'Открыть меню', exact: true })
  if (await mobileMenu.isVisible()) {
    await mobileMenu.click()
    await page.getByRole('dialog').getByRole('button', { name: label, exact: true }).click()
    return
  }
  const group: Record<string, string> = {
    'Комплекты и сравнение': 'Изменения',
    'Кадровые события': 'Изменения',
    'Реестр функций': 'Ещё',
    'Поиск по содержанию': 'Ещё',
    'О проекте': 'Ещё',
    'Моё пространство': 'Меню пользователя',
  }
  if (group[label]) {
    await page.getByRole('button', { name: group[label], exact: true }).click()
    await page.getByRole('menuitem', { name: label, exact: true }).click()
  } else {
    await page
      .getByRole('navigation', { name: 'Основная навигация' })
      .getByRole('button', { name: label, exact: true })
      .click()
  }
}

async function json(page: Page, path: string) {
  const response = await page.request.get('/api/v1' + path)
  expect(response.ok()).toBe(true)
  return response.json()
}

async function capture(page: Page, file: string) {
  if (await page.locator('.page-content').count()) {
    await expect
      .poll(() => page.locator('.page-content').evaluate((el) => getComputedStyle(el).opacity))
      .toBe('1')
  }
  if (await page.locator('.inspector > div').count()) {
    await expect
      .poll(() =>
        page
          .locator('.inspector > div')
          .first()
          .evaluate((el) => getComputedStyle(el).opacity),
      )
      .toBe('1')
  }
  const graphViewport = page.locator('.react-flow__viewport')
  if (await graphViewport.count()) {
    let previous = ''
    let stableSamples = 0
    await expect
      .poll(
        async () => {
          const transform = await graphViewport.evaluate((el) => getComputedStyle(el).transform)
          stableSamples = transform === previous ? stableSamples + 1 : 0
          previous = transform
          return stableSamples
        },
        { intervals: [100, 100, 100] },
      )
      .toBeGreaterThanOrEqual(2)
  }
  await expect(page.locator('[data-sonner-toast]')).toHaveCount(0, { timeout: 8000 })
  const hasVisibleDialog = (await page.locator('[role="dialog"]:visible').count()) > 0
  if (!hasVisibleDialog) {
    await page.evaluate(() => window.scrollTo(0, 0))
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0)
  }
  await page.screenshot({
    path: '/screenshots/' + file,
    fullPage: !hasVisibleDialog,
    animations: 'disabled',
  })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
}

async function basis(page: Page, title: string, quote: string) {
  await page.getByLabel('Название и номер документа').fill(title)
  await page.getByLabel('Дата документа', { exact: true }).fill('2026-10-19')
  await page.getByRole('textbox', { name: 'Точная выдержка', exact: true }).fill(quote)
  await page.getByLabel('Подтверждаю полномочия').check()
  await page.getByRole('button', { name: 'Зарегистрировать событие', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
}

test('A/D: расширенный состав → списки и схема → событие → очередь → замена → сохранение и изоляция', async ({
  browser,
  page,
}) => {
  test.setTimeout(300_000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Войти в OrgProekt' })).toBeVisible()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    await capture(page, `01-login-${width}.png`)
    const box = await page.locator('form').first().boundingBox()
    expect(box).not.toBeNull()
    expect(Math.abs(box!.x + box!.width / 2 - width / 2)).toBeLessThan(30)
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await signIn(page, 'hr')
  await page.getByLabel('Дата рабочего обзора').fill(day)
  const overview = await json(page, `/overview?as_of=${day}&limit=6`)
  expect(overview.attention.total).toBeGreaterThan(0)
  const firstIssue = overview.attention.items[0]
  await expect(page.locator('.issue-card')).toHaveCount(overview.attention.items.length)
  await expect(
    page.locator('.issue-detail').getByRole('heading', {
      name: firstIssue.title,
      exact: true,
    }),
  ).toBeVisible()
  await expect(page.locator('.sidebar')).toHaveCount(0)
  await expect(page.locator('.topbar img')).toHaveCount(0)
  await expect(page.locator('.topbar')).not.toContainText('Название организации')

  // The new dropdown works entirely from the keyboard and restores visible focus on Escape.
  const changesTrigger = page.getByRole('button', { name: 'Изменения', exact: true })
  await changesTrigger.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menuitem', { name: 'Предложения', exact: true })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(
    page.getByRole('menuitem', { name: 'Комплекты и сравнение', exact: true }),
  ).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: 'Кадровые события', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(changesTrigger).toBeFocused()
  expect(
    await changesTrigger.evaluate((el) => {
      const style = getComputedStyle(el)
      return (
        el.matches(':focus-visible') &&
        style.outlineStyle !== 'none' &&
        parseFloat(style.outlineWidth) >= 2
      )
    }),
  ).toBe(true)

  // Selection has a real detail request. While it is pending, no previous evidence is retained.
  if (overview.attention.items.length > 1) {
    const nextIssue = overview.attention.items[1]
    let releaseDetail!: () => void
    let detailRequested!: () => void
    const heldDetail = new Promise<void>((resolve) => {
      releaseDetail = resolve
    })
    const requestedDetail = new Promise<void>((resolve) => {
      detailRequested = resolve
    })
    const detailPattern = `**/api/v1/functions/${nextIssue.id}?**`
    await page.route(
      detailPattern,
      async (route) => {
        detailRequested()
        await heldDetail
        await route.continue()
      },
      { times: 1 },
    )
    await page
      .getByRole('button', { name: `Открыть вопрос: ${nextIssue.title}`, exact: true })
      .click()
    await requestedDetail
    await expect(
      page.locator('.issue-detail').getByRole('heading', {
        name: firstIssue.title,
        exact: true,
      }),
    ).toHaveCount(0)
    releaseDetail()
    await expect(
      page.locator('.issue-detail').getByRole('heading', {
        name: nextIssue.title,
        exact: true,
      }),
    ).toBeVisible()
    await expect(
      page.getByRole('button', {
        name: `Открыть вопрос: ${nextIssue.title}`,
        exact: true,
      }),
    ).toHaveAttribute('aria-pressed', 'true')
    await page.unroute(detailPattern)
  }

  // A filtered question opens its exact API source, not the previous selection's quote.
  await page.getByLabel('Найти вопрос', { exact: true }).fill(firstIssue.code)
  await expect(page.locator('.issue-card')).toHaveCount(1)
  await expect(
    page.locator('.issue-detail').getByRole('heading', {
      name: firstIssue.title,
      exact: true,
    }),
  ).toBeVisible()
  const overviewSource = await json(page, `/sources/${firstIssue.source_anchor_id}`)
  await page
    .locator('.issue-detail')
    .getByRole('button', {
      name: 'Открыть источник',
      exact: true,
    })
    .click()
  await expect(page.locator('.source-dialog .source-quote')).toContainText(overviewSource.quote)
  await capture(page, '16-overview-source.png')
  await page.keyboard.press('Escape')
  await page.getByLabel('Найти вопрос', { exact: true }).fill('ВОПРОСА НЕ СУЩЕСТВУЕТ')
  await expect(page.locator('.issue-card')).toHaveCount(0)
  await expect(
    page.locator('.issue-detail').getByRole('heading', {
      name: firstIssue.title,
      exact: true,
    }),
  ).toHaveCount(0)
  await page.getByLabel('Найти вопрос', { exact: true }).fill('')
  await page
    .getByLabel('Подразделение вопросов', { exact: true })
    .selectOption(firstIssue.department_id)
  const scopedOverview = await json(
    page,
    `/overview?as_of=${day}&department_id=${firstIssue.department_id}&limit=6`,
  )
  await expect(page.locator('.issue-card')).toHaveCount(scopedOverview.attention.items.length)
  for (const item of scopedOverview.attention.items) {
    await expect(
      page.getByRole('button', { name: `Открыть вопрос: ${item.title}`, exact: true }),
    ).toBeVisible()
  }
  await page.getByLabel('Подразделение вопросов', { exact: true }).selectOption('')
  await page.getByRole('tab', { name: 'Нет исполнителя', exact: true }).click()
  const uncoveredOverview = await json(page, `/overview?as_of=${day}&coverage=uncovered&limit=6`)
  await expect(page.locator('.issue-card')).toHaveCount(uncoveredOverview.attention.items.length)
  await page.getByRole('tab', { name: /^Все вопросы/ }).click()

  // The shared date changes the snapshot and the overview together, with visible loading.
  let releaseSnapshot!: () => void
  let snapshotRequested!: () => void
  const heldSnapshot = new Promise<void>((resolve) => {
    releaseSnapshot = resolve
  })
  const requestedSnapshot = new Promise<void>((resolve) => {
    snapshotRequested = resolve
  })
  await page.route(
    '**/api/v1/organization?as_of=2026-10-19',
    async (route) => {
      snapshotRequested()
      await heldSnapshot
      await route.continue()
    },
    { times: 1 },
  )
  await page.getByLabel('Дата рабочего обзора').fill('2026-10-19')
  await requestedSnapshot
  await expect(page.locator('.issue-card')).toHaveCount(0)
  releaseSnapshot()
  const earlierOverview = await json(page, '/overview?as_of=2026-10-19&limit=6')
  await expect(page.locator('.issue-card')).toHaveCount(earlierOverview.attention.items.length)
  await page.unroute('**/api/v1/organization?as_of=2026-10-19')
  await page.getByLabel('Дата рабочего обзора').fill(day)
  await expect(page.locator('.issue-card')).toHaveCount(overview.attention.items.length)
  await page
    .getByRole('button', { name: `Открыть вопрос: ${firstIssue.title}`, exact: true })
    .click()
  await expect(
    page.locator('.issue-detail').getByRole('heading', {
      name: firstIssue.title,
      exact: true,
    }),
  ).toBeVisible()
  for (const width of [1920, 1440, 1280, 1024, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    await capture(page, `16-work-overview-${width}.png`)
    const main = await page.locator('.main-content').boundingBox()
    expect(main!.width).toBeLessThanOrEqual(1400)
  }
  // Exercise the narrow navigation instead of only resizing a static page.
  await navigate(page, 'Документы')
  await expect(page.getByRole('heading', { name: 'Документы', exact: true })).toBeVisible()
  await navigate(page, 'Обзор')
  await expect(page.getByRole('heading', { name: 'Рабочий обзор', exact: true })).toBeVisible()
  await page.setViewportSize({ width: 1440, height: 1000 })

  // PostgreSQL-backed paging, scoped totals, search by person and job title.
  const first = await json(page, `/directory/employees?as_of=${day}&limit=5&offset=0`)
  const second = await json(page, `/directory/employees?as_of=${day}&limit=5&offset=5`)
  expect(first.total).toBe(26)
  expect(first.items).toHaveLength(5)
  expect(second.items).toHaveLength(5)
  expect(new Set([...first.items, ...second.items].map((p: { id: string }) => p.id)).size).toBe(10)
  const departments = await json(page, `/departments?as_of=${day}&limit=100`)
  expect(departments.total).toBe(5)
  expect(
    Object.fromEntries(
      departments.items.map((d: { code: string; employee_count: number }) => [
        d.code,
        d.employee_count,
      ]),
    ),
  ).toEqual({ DIR: 3, IT: 5, HR: 5, FIN: 5, AUD: 5 })
  const itDepartment = departments.items.find((d: { code: string }) => d.code === 'IT')
  expect(
    (await json(page, `/directory/employees?department_id=${itDepartment.id}&as_of=${day}`)).total,
  ).toBe(6)
  expect(
    (
      await json(
        page,
        `/directory/employees?as_of=${day}&q=${encodeURIComponent('Таисия Снежная')}`,
      )
    ).total,
  ).toBe(1)
  expect(
    (
      await json(
        page,
        `/directory/employees?as_of=${day}&q=${encodeURIComponent('технической поддержки')}`,
      )
    ).total,
  ).toBe(2)
  const invalid = await page.request.get('/api/v1/directory/employees?limit=101')
  expect(invalid.status()).toBe(422)
  const invalidBody = await invalid.json()
  for (const key of ['code', 'message', 'correlation_id']) expect(invalidBody).toHaveProperty(key)

  // A real editable setting persists through a complete reload, then is restored.
  const initialSettings = await json(page, '/organization/settings')
  expect(initialSettings.name).toBeNull()
  await navigate(page, 'Моё пространство')
  await page.getByLabel('Отображаемое название').fill('Учебная организация — E2E')
  await page.getByRole('button', { name: 'Сохранить название', exact: true }).click()
  await expect
    .poll(async () => (await json(page, '/organization/settings')).name)
    .toBe('Учебная организация — E2E')
  await page.reload()
  await expect(page.locator('.topbar')).toContainText('Учебная организация — E2E')
  await navigate(page, 'Моё пространство')
  await page.getByLabel('Отображаемое название').fill('')
  await page.getByRole('button', { name: 'Сохранить название', exact: true }).click()
  await expect.poll(async () => (await json(page, '/organization/settings')).name).toBeNull()

  // Registry actions open the exact persisted source selected through their UI filters.
  const documents = await json(page, '/documents?limit=100')
  const registryDocument = documents.items.find(
    (document: { source_anchor_ids: string[] }) => document.source_anchor_ids.length > 0,
  )
  expect(registryDocument).toBeDefined()
  const documentSource = await json(page, `/sources/${registryDocument.source_anchor_ids[0]}`)
  await navigate(page, 'Документы')
  await page.getByLabel('Поиск документов').fill(registryDocument.title)
  const documentRow = page.getByRole('row').filter({
    has: page.getByText(registryDocument.title, { exact: true }),
  })
  await expect(documentRow).toHaveCount(1)
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await capture(page, '14-documents-1440.png')
  await documentRow.getByRole('button', { name: 'Фрагмент 1', exact: true }).click()
  const registrySource = page.getByRole('dialog', { name: 'Документальное основание', exact: true })
  await expect(registrySource).toBeVisible()
  await expect(
    registrySource.getByRole('heading', { name: documentSource.title, exact: true }),
  ).toBeVisible()
  await expect(registrySource.locator('.source-quote')).toContainText(documentSource.quote)
  await page.keyboard.press('Escape')
  await expect(registrySource).toHaveCount(0)

  const coveredFunctions = await json(page, `/functions?as_of=${day}&coverage=covered&limit=10`)
  expect(coveredFunctions.total).toBeGreaterThan(0)
  await navigate(page, 'Реестр функций')
  await page.getByLabel('Покрытие функций').selectOption('covered')
  const functionRegistry = page.locator('.function-registry')
  await expect(functionRegistry.locator('tbody tr')).toHaveCount(
    Math.min(10, coveredFunctions.total),
  )
  await expect(functionRegistry.locator('tbody .status-covered')).toHaveCount(
    Math.min(10, coveredFunctions.total),
  )
  await expect(functionRegistry.locator('.pagination')).toContainText(
    `1–${Math.min(10, coveredFunctions.total)} из ${coveredFunctions.total}`,
  )
  if (coveredFunctions.total > 10) {
    const nextFunctions = await json(
      page,
      `/functions?as_of=${day}&coverage=covered&limit=10&offset=10`,
    )
    await page.getByRole('button', { name: 'Следующая страница', exact: true }).click()
    await expect(functionRegistry.locator('.pagination')).toContainText(
      `11–${Math.min(20, coveredFunctions.total)} из ${coveredFunctions.total}`,
    )
    await expect(
      functionRegistry.getByText(nextFunctions.items[0].code, { exact: true }),
    ).toBeVisible()
  }
  const registryFunction = coveredFunctions.items[0]
  const functionSource = await json(page, `/sources/${registryFunction.source_anchor_id}`)
  await page.getByLabel('Поиск по функциям').fill(registryFunction.code)
  await expect(functionRegistry.locator('tbody tr')).toHaveCount(1)
  await expect(functionRegistry.getByText(registryFunction.title, { exact: true })).toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Предыдущая страница', exact: true }),
  ).toBeDisabled()
  await capture(page, '15-functions-1440.png')
  await page
    .getByRole('button', { name: `Основание ${registryFunction.code}`, exact: true })
    .click()
  await expect(registrySource).toBeVisible()
  await expect(
    registrySource.getByRole('heading', { name: functionSource.title, exact: true }),
  ).toBeVisible()
  await expect(registrySource.locator('.source-quote')).toContainText(functionSource.quote)
  await page.keyboard.press('Escape')
  await expect(registrySource).toHaveCount(0)

  await navigate(page, 'Организация')
  await expect(page.locator('.department-row')).toHaveCount(5)
  await expect(page.locator('.react-flow')).toHaveCount(0)
  await page
    .locator('.department-row')
    .filter({ hasText: 'Информационные технологии' })
    .getByRole('button', { name: 'Раскрыть состав', exact: true })
    .click()
  await expect(page.getByRole('row').filter({ hasText: 'Таисия Снежная' })).toBeVisible()
  await page.route('**/api/v1/directory/employees?**', (route) => route.abort('failed'), {
    times: 1,
  })
  await page.getByLabel('Поиск по сотрудникам и должностям').fill('Мирон Дубравин')
  await expect(page.getByRole('alert')).toContainText('Нет связи с сервером')
  await capture(page, '12-network-error-1440.png')
  await page.getByRole('button', { name: 'Повторить', exact: true }).click()
  await expect(page.getByRole('row').filter({ hasText: 'Мирон Дубравин' })).toBeVisible()
  await page.getByLabel('Поиск по сотрудникам и должностям').fill('')
  await expect(page.getByRole('row').filter({ hasText: 'Таисия Снежная' })).toBeVisible()
  for (const width of [1440, 1280, 1024, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    await capture(page, `02-organization-${width}.png`)
  }
  await page.getByLabel('Поиск по сотрудникам и должностям').fill('НЕСУЩЕСТВУЮЩИЙ СОТРУДНИК')
  await expect(page.getByRole('heading', { name: 'Совпадений нет' })).toBeVisible()
  await capture(page, '08-empty-390.png')
  await page.getByLabel('Поиск по сотрудникам и должностям').fill('Илья Ветров')
  await page.getByRole('button', { name: 'Открыть карточку: Илья Ветров', exact: true }).click()
  const mobileEmployeeCard = page.getByRole('dialog', { name: 'Илья Ветров', exact: true })
  await expect(mobileEmployeeCard).toBeVisible()
  await expect(mobileEmployeeCard).toContainText('Назначения и история')
  await capture(page, '10-employee-390.png')
  await mobileEmployeeCard.getByRole('button', { name: 'Основание', exact: true }).first().click()
  await expect(
    page.getByRole('dialog', { name: 'Документальное основание', exact: true }),
  ).toBeVisible()
  await expect(page.locator('.source-quote')).toContainText('Илья Ветров')
  await capture(page, '13-employee-source-390.png')
  await page.keyboard.press('Escape')
  await expect(
    page.getByRole('dialog', { name: 'Документальное основание', exact: true }),
  ).toHaveCount(0)
  await expect(mobileEmployeeCard).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(mobileEmployeeCard).toHaveCount(0)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page
    .getByRole('row')
    .filter({ hasText: 'Илья Ветров' })
    .getByRole('button', { name: 'Карточка', exact: true })
    .click()
  await expect(page.getByRole('dialog', { name: 'Илья Ветров', exact: true })).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('Назначения и история')
  await capture(page, '10-employee-1440.png')
  await page.getByRole('button', { name: 'Штатная единица', exact: true }).click()
  await expect(
    page.locator('.inspector').getByRole('heading', { name: 'Инженер инфраструктуры' }),
  ).toBeVisible()

  await page.getByLabel('Поиск по сотрудникам и должностям').fill('')
  await page.getByRole('button', { name: 'Схема', exact: true }).click()
  await page.getByLabel('Поиск по структуре').fill('Таисия Снежная')
  await expect(page.locator('.employee-node').filter({ hasText: 'Таисия Снежная' })).toBeVisible()
  await page.getByLabel('Поиск по структуре').fill('Тимур Ясный')
  const unassignedTimur = page.locator('.employee-node').filter({ hasText: 'Тимур Ясный' })
  await expect(unassignedTimur).toBeVisible()
  await expect(unassignedTimur).toContainText('Без назначения на дату')
  await page.getByRole('button', { name: 'Открыть карточку: Тимур Ясный', exact: true }).click()
  const timurCard = page.getByRole('dialog', { name: 'Тимур Ясный', exact: true })
  await expect(timurCard).toBeVisible()
  await expect(timurCard).toContainText('Назначения не зарегистрированы.')
  await page.keyboard.press('Escape')
  await expect(timurCard).toHaveCount(0)
  await page.getByLabel('Поиск по структуре').fill('')
  await expect(page.locator('.department-node')).toHaveCount(5)
  await page.getByRole('button', { name: 'Вся структура', exact: true }).click()
  await expect(
    page.getByRole('button', { name: /^(Свернуть|Раскрыть) Информационные технологии$/ }),
  ).toBeVisible()
  if (
    await page
      .getByRole('button', { name: 'Свернуть Информационные технологии', exact: true })
      .isVisible()
  ) {
    await page
      .getByRole('button', { name: 'Свернуть Информационные технологии', exact: true })
      .click()
  }
  await expect(
    page.getByRole('button', { name: 'Раскрыть Информационные технологии', exact: true }),
  ).toBeVisible()
  await page
    .getByRole('button', { name: 'Раскрыть Информационные технологии', exact: true })
    .click()
  await expect(page.locator('.position-node')).toHaveCount(4)
  await page.getByRole('button', { name: 'Вся структура', exact: true }).click()
  await page.locator('.seat-node').filter({ hasText: 'IT-01' }).click()
  await expect(
    page.locator('.inspector').getByRole('heading', { name: 'Инженер инфраструктуры' }),
  ).toBeVisible()
  await capture(page, '11-graph-1440.png')
  for (const width of [1280, 1024]) {
    await page.setViewportSize({ width, height: 1000 })
    await expect(
      page.locator('.inspector').getByRole('heading', { name: 'Инженер инфраструктуры' }),
    ).toBeVisible()
    if (width === 1024) {
      await expect(
        page.getByRole('dialog').filter({ has: page.locator('.inspector') }),
      ).toBeVisible()
    }
    await capture(page, `11-graph-inspector-${width}.png`)
  }
  await page
    .locator('.inspector')
    .getByRole('button', { name: 'Закрыть инспектор', exact: true })
    .click()
  await expect(page.locator('.inspector')).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: 'Вся структура', exact: true }).click()
  await page.locator('.seat-node').filter({ hasText: 'IT-01' }).click()
  await expect(
    page.locator('.inspector').getByRole('heading', { name: 'Инженер инфраструктуры' }),
  ).toBeVisible()

  await page.locator('.inspector').getByRole('button', { name: 'Прекратить назначение' }).click()
  await page.getByLabel('Первый день без назначения').fill('2026-10-20')
  await basis(
    page,
    'Учебный приказ № 20 — прекращение',
    'Прекратить назначение Ильи Ветрова на штатную единицу IT-01 с 20 октября 2026 года. Синтетическое основание.',
  )
  await expect(page.locator('.inspector').getByText('Штатная единица вакантна')).toBeVisible()
  await expect
    .poll(async () => (await json(page, `/functions?as_of=${day}&coverage=uncovered`)).total)
    .toBe(3)
  await capture(page, '03-coverage-gap-1440.png')
  await page
    .locator('.inspector')
    .getByRole('button', { name: 'Открыть основание' })
    .first()
    .click()
  await expect(page.locator('.source-quote')).toContainText('восстановление систем')
  await capture(page, '04-source.png')
  await page.keyboard.press('Escape')

  const auditorContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
  })
  const auditor = await auditorContext.newPage()
  auditor.on('pageerror', (e) => errors.push(e.message))
  await signIn(auditor, 'auditor')
  await auditor.getByLabel('Дата рабочего обзора').fill(day)
  const affectedOverview = await json(auditor, `/overview?as_of=${day}&limit=6`)
  expect(affectedOverview.attention.items.length).toBeGreaterThan(1)
  const previousQuestion = affectedOverview.attention.items[0]
  const selectedQuestion = affectedOverview.attention.items[1]
  await expect(
    auditor.locator('.issue-detail').getByRole('heading', {
      name: previousQuestion.title,
      exact: true,
    }),
  ).toBeVisible()
  let releaseQuestion!: () => void
  let questionRequested!: () => void
  const heldQuestion = new Promise<void>((resolve) => {
    releaseQuestion = resolve
  })
  const requestedQuestion = new Promise<void>((resolve) => {
    questionRequested = resolve
  })
  const questionPattern = `**/api/v1/functions/${selectedQuestion.id}?**`
  await auditor.route(
    questionPattern,
    async (route) => {
      questionRequested()
      await heldQuestion
      await route.continue()
    },
    { times: 1 },
  )
  await auditor
    .getByRole('button', {
      name: `Открыть вопрос: ${selectedQuestion.title}`,
      exact: true,
    })
    .click()
  await requestedQuestion
  await expect(
    auditor.locator('.issue-detail').getByRole('heading', {
      name: previousQuestion.title,
      exact: true,
    }),
  ).toHaveCount(0)
  await expect(auditor.locator('.issue-detail')).toHaveAttribute('aria-busy', 'true')
  releaseQuestion()
  await expect(
    auditor.locator('.issue-detail').getByRole('heading', {
      name: selectedQuestion.title,
      exact: true,
    }),
  ).toBeVisible()
  await auditor.unroute(questionPattern)
  const questionSource = await json(auditor, `/sources/${selectedQuestion.source_anchor_id}`)
  await expect(auditor.locator('.detail-evidence')).toContainText(questionSource.quote)
  await auditor.getByRole('tab', { name: 'История покрытия', exact: true }).click()
  await expect(auditor.locator('.detail-history')).toContainText('Нет исполнителя')
  await auditor.getByRole('tab', { name: 'Основание', exact: true }).click()
  // Regression: the selected line tab must retain the agreed green underline on the dark panel.
  const basisTab = auditor.getByRole('tab', { name: 'Основание', exact: true })
  await expect(basisTab).toHaveAttribute('aria-selected', 'true')
  await expect
    .poll(() =>
      basisTab.evaluate((el) => {
        const underline = getComputedStyle(el)
        return {
          color: underline.borderBottomColor,
          width: underline.borderBottomWidth,
          style: underline.borderBottomStyle,
        }
      }),
    )
    .toEqual({ color: 'rgb(40, 242, 138)', width: '2px', style: 'solid' })
  // Show a real three-question state after the registered personnel event.
  for (const width of [1920, 1440, 1280, 1024, 390]) {
    await auditor.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
    await capture(auditor, `17-auditor-overview-${width}.png`)
  }
  await auditor.setViewportSize({ width: 1440, height: 1000 })
  await auditor.getByRole('button', { name: 'Открыть уведомления', exact: true }).click()
  await expect(auditor.getByRole('heading', { name: 'Назначение прекращено' })).toBeVisible({
    timeout: 30_000,
  })
  await auditor.getByRole('button', { name: 'Открыть контекст' }).click()
  await expect(
    auditor.locator('.inspector').getByRole('button', { name: 'Прекратить назначение' }),
  ).toHaveCount(0)
  expect((await json(auditor, '/notifications')).items).toHaveLength(1)

  await page.locator('.inspector').getByRole('button', { name: 'Назначить исполнителя' }).click()
  await page.getByLabel('Сотрудник', { exact: true }).selectOption({ label: 'Тимур Ясный' })
  await page.getByLabel('Вид назначения').selectOption('temporary')
  await page.getByLabel('Начало назначения').fill('2026-10-25')
  await page.getByLabel('Первый день после замещения').fill('2026-11-01')
  await basis(
    page,
    'Учебный приказ № 25 — замещение',
    'Назначить Тимура Ясного на IT-01 с 25 октября до 1 ноября 2026 года, не включая дату окончания. Синтетический документ.',
  )
  await expect(page.locator('.inspector').getByText('Тимур Ясный', { exact: true })).toBeVisible()
  await expect
    .poll(async () => (await json(page, `/functions?as_of=${day}&coverage=uncovered`)).total)
    .toBe(1)
  await page.locator('.inspector').getByText('Периоды покрытия', { exact: true }).first().click()
  await expect(
    page.locator('.inspector .period').filter({ hasText: 'Нет исполнителя' }),
  ).toHaveCount(2)
  await navigate(page, 'Кадровые события')
  await expect(
    page.getByRole('heading', { name: 'Назначение прекращено', exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole('heading', { name: 'Зарегистрировано назначение', exact: true }),
  ).toBeVisible()
  await capture(page, '05-history-1440.png')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: 'Кадровые события', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Открыть меню', exact: true })).toBeVisible()
  await capture(page, '05-history-390.png')
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.reload()
  const events = await json(page, '/personnel/events')
  expect(events.total).toBe(2)
  expect(events.items.map((event: { kind: string }) => event.kind).sort()).toEqual([
    'appoint',
    'terminate',
  ])

  const restrictedContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
  })
  const restricted = await restrictedContext.newPage()
  restricted.on('pageerror', (e) => errors.push(e.message))
  await signIn(restricted, 'finance')
  await restricted.getByLabel('Дата рабочего обзора').fill(day)
  const restrictedOverview = await json(restricted, `/overview?as_of=${day}&limit=6`)
  await expect(restricted.locator('.issue-card')).toHaveCount(
    restrictedOverview.attention.items.length,
  )
  await expect(restricted.getByLabel('Подразделение вопросов').locator('option')).toHaveCount(2)
  await expect(restricted.locator('.work-overview')).not.toContainText('Информационные технологии')
  await capture(restricted, '18-overview-department-isolation.png')
  expect((await json(restricted, '/directory/employees?limit=5')).total).toBe(6)
  expect(
    (await json(restricted, `/directory/employees?q=${encodeURIComponent('Илья Ветров')}`)).total,
  ).toBe(0)
  expect((await json(restricted, `/departments?as_of=${day}`)).total).toBe(1)
  const hrData = await json(page, `/organization?as_of=${day}`)
  const itFunction = hrData.functions.find((f: { code: string }) => f.code === 'IT-F01')
  const itEmployee = (
    await json(page, `/directory/employees?q=${encodeURIComponent('Илья Ветров')}`)
  ).items[0]
  for (const path of [
    `/sources/${itFunction.source_anchor_id}`,
    `/functions/${itFunction.id}`,
    `/personnel/events?department_id=${itDepartment.id}`,
    `/directory/employees?department_id=${itDepartment.id}`,
    `/directory/employees/${itEmployee.id}`,
    `/documents?department_id=${itDepartment.id}`,
    `/overview?department_id=${itDepartment.id}`,
  ]) {
    const denied = await restricted.request.get('/api/v1' + path)
    expect(denied.status()).toBe(404)
    expect(await denied.json()).toHaveProperty('correlation_id')
  }
  await navigate(restricted, 'Организация')
  await expect(restricted.locator('.department-row')).toHaveCount(1)
  await expect(restricted.locator('.department-row')).toContainText('Финансы и планирование')
  await capture(restricted, '06-department-isolation.png')
  await restricted.emulateMedia({ reducedMotion: 'reduce' })
  await restricted.getByRole('button', { name: 'Схема', exact: true }).click()
  await expect(restricted.locator('.department-node')).toHaveCount(1)
  expect(
    await restricted.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
  ).toBe(true)
  // Stage 2: real multipart upload → durable worker → exact source → download → immutable revision.
  await navigate(page, 'Документы')
  await page.getByRole('button', { name: 'Загрузить документ', exact: true }).click()
  await page
    .getByLabel('Файл', { exact: true })
    .setInputFiles('e2e/fixtures/synthetic-regulation.docx')
  await page.getByLabel('Название документа', { exact: true }).fill('Учебный регламент — загрузка')
  await page
    .getByRole('dialog')
    .getByLabel('Подразделение', { exact: true })
    .selectOption(itDepartment.id)
  await page.getByLabel('Все сведения в файле вымышлены').check()
  await capture(page, '07-document-upload-1440.png')
  await page.getByRole('button', { name: 'Загрузить и обработать', exact: true }).click()
  await expect(
    page
      .getByRole('region', { name: 'Состояние обработки' })
      .getByText('Текст извлечён', { exact: true }),
  ).toBeVisible({ timeout: 40000 })
  const uploaded = (
    await json(page, `/documents?q=${encodeURIComponent('Учебный регламент — загрузка')}`)
  ).items[0]
  const detail = await json(page, `/documents/${uploaded.id}`)
  expect(detail.external_policy).toBe('prohibited')
  await page.getByLabel('Поиск по тексту документа').fill('реестр')
  await expect(page.locator('.document-fragment')).toHaveCount(1)
  await capture(page, '08-document-ready-1440.png')
  await page.setViewportSize({ width: 390, height: 844 })
  await capture(page, '08-document-ready-390.png')
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: 'Открыть источник', exact: true }).click()
  await expect(page.locator('.source-dialog mark')).toHaveText(
    'Учебный отдел ведёт реестр документов и проверяет их редакции.',
  )
  await capture(page, '09-document-source-1440.png')
  await page.getByRole('button', { name: 'Закрыть источник' }).click()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Скачать оригинал' }).click()
  const download = await downloadPromise
  expect(
    createHash('sha256')
      .update(readFileSync((await download.path())!))
      .digest('hex'),
  ).toBe(detail.sha256)
  await page.getByRole('button', { name: 'Загрузить новую редакцию' }).click()
  await page
    .getByLabel('Файл', { exact: true })
    .setInputFiles('e2e/fixtures/synthetic-regulation.pdf')
  await page.getByLabel('Редакция', { exact: true }).fill('2')
  await page.getByLabel('Все сведения в файле вымышлены').check()
  await page.getByRole('button', { name: 'Загрузить и обработать', exact: true }).click()
  await expect(
    page
      .getByRole('region', { name: 'Состояние обработки' })
      .getByText('Текст извлечён', { exact: true }),
  ).toBeVisible({ timeout: 40000 })
  await expect(page.locator('.document-version')).toHaveCount(2)
  await expect(page.locator('.document-fragment').first()).toContainText('Страница 1')
  const anchors = await json(page, `/documents/${uploaded.id}/fragments`)
  for (const path of [
    `/documents/${uploaded.id}`,
    `/documents/${uploaded.id}/file`,
    `/documents/${uploaded.id}/fragments`,
    `/documents/${uploaded.id}/versions`,
    `/sources/${anchors.items[0].id}`,
  ]) {
    expect((await restricted.request.get('/api/v1' + path)).status()).toBe(404)
  }
  await page.reload()
  await navigate(page, 'Документы')
  await page.getByLabel('Поиск документов').fill('Учебный регламент — загрузка')
  await expect(page.locator('tbody tr')).toHaveCount(2)
  expect((await json(page, `/documents/${uploaded.id}`)).sha256).toBe(detail.sha256)
  expect((await json(page, `/documents/${uploaded.id}/versions`)).total).toBe(2)
  await capture(page, '10-document-history.png')
  await page
    .getByRole('button', { name: 'Учебный регламент — загрузка', exact: true })
    .first()
    .click()
  await page.getByRole('button', { name: 'Загрузить новую редакцию' }).click()
  await page
    .getByLabel('Файл', { exact: true })
    .setInputFiles('e2e/fixtures/synthetic-regulation-after.docx')
  await page.getByLabel('Редакция', { exact: true }).fill('3')
  await page.getByLabel('Все сведения в файле вымышлены').check()
  await page.getByRole('button', { name: 'Загрузить и обработать', exact: true }).click()
  await expect(
    page
      .getByRole('region', { name: 'Состояние обработки' })
      .getByText('Текст извлечён', { exact: true }),
  ).toBeVisible({ timeout: 40000 })
  await expect(page.locator('.document-version')).toHaveCount(3)
  // Fixed before/after corpora, real local comparison, immutable human review and hybrid search.
  await navigate(auditor, 'Комплекты и сравнение')
  await auditor.getByRole('button', { name: 'Комплекты', exact: true }).click()
  for (const revision of ['1', '3']) {
    await auditor.getByRole('button', { name: 'Новый комплект', exact: true }).click()
    await auditor
      .getByLabel('Название комплекта', { exact: true })
      .fill(`Учебный комплект ${revision === '1' ? 'до' : 'после'}`)
    await auditor.getByLabel('Выбрать редакции документов').fill('Учебный регламент — загрузка')
    await expect(auditor.locator('.analysis-pick-list .document-choice')).toHaveCount(3)
    await auditor
      .locator('.document-choice')
      .filter({ hasText: `Редакция ${revision} ·` })
      .getByRole('checkbox')
      .check()
    await auditor.getByRole('button', { name: 'Зафиксировать комплект', exact: true }).click()
    await expect(auditor.getByRole('dialog')).toHaveCount(0)
  }
  await expect(auditor.locator('tbody tr')).toHaveCount(2)
  const corpora = (await json(auditor, '/corpora')).items
  const beforeCorpus = corpora.find((c: { title: string }) => c.title === 'Учебный комплект до')
  const afterCorpus = corpora.find((c: { title: string }) => c.title === 'Учебный комплект после')
  expect(beforeCorpus.documents[0].id).toBe(uploaded.id)
  expect(afterCorpus.documents[0].revision).toBe('3')
  await capture(auditor, '11-corpora-1440.png')
  await auditor.getByRole('button', { name: 'Сравнения', exact: true }).click()
  await auditor.getByRole('button', { name: 'Новое сравнение', exact: true }).click()
  await auditor.getByLabel('Название сравнения').fill('Учебная проверка до и после')
  for (const side of ['до', 'после']) {
    await auditor.getByRole('button', { name: `Комплект ${side}`, exact: true }).click()
    await auditor
      .getByRole('dialog')
      .last()
      .getByRole('button', { name: `Учебный комплект ${side}` })
      .click()
  }
  await auditor.getByRole('button', { name: 'Начать сравнение', exact: true }).click()
  await expect(auditor.locator('.finding-card').first()).toBeVisible({ timeout: 60000 })
  await expect(auditor.locator('.analysis-job')).toContainText('Обработка завершена')
  const savedRun = (await json(auditor, '/comparisons')).items[0]
  expect(savedRun.job.result_summary.external_calls).toBe(0)
  expect(savedRun.before.digest).toBe(beforeCorpus.digest)
  await auditor.getByLabel('Категория сравнения').selectOption('conflict')
  await expect(auditor.locator('.finding-card')).toHaveCount(1)
  await auditor.locator('.word-diff summary').click()
  await expect(auditor.locator('.word-diff ins')).toContainText('не')
  await capture(auditor, '12-comparison-1440.png')
  await auditor.setViewportSize({ width: 390, height: 844 })
  await capture(auditor, '12-comparison-390.png')
  await auditor.setViewportSize({ width: 1440, height: 1000 })
  await auditor
    .locator('.finding-card')
    .first()
    .getByRole('button', { name: 'Рассмотреть', exact: true })
    .click()
  await auditor
    .getByRole('textbox', { name: 'Основание решения', exact: true })
    .fill('Требуется проверка утверждения обеих учебных редакций и полноты комплекта.')
  await auditor.getByRole('button', { name: 'Сохранить рассмотрение', exact: true }).click()
  await expect(auditor.locator('.finding-card').first()).toContainText(
    'Нужны дополнительные основания · Вера Северова',
  )
  await auditor.reload()
  await navigate(auditor, 'Комплекты и сравнение')
  await auditor.getByRole('button', { name: 'Учебная проверка до и после', exact: true }).click()
  await auditor.getByLabel('Категория сравнения').selectOption('conflict')
  await expect(auditor.locator('.finding-card').first()).toContainText(
    'Нужны дополнительные основания · Вера Северова',
  )
  await auditor
    .locator('.finding-card')
    .first()
    .getByRole('button', { name: 'Рассмотреть', exact: true })
    .click()
  await expect(auditor.locator('.review-history')).toContainText('Требуется проверка утверждения')
  await capture(auditor, '13-review-history.png')
  await auditor.keyboard.press('Escape')
  await navigate(auditor, 'Поиск по содержанию')
  await auditor.getByText('Комплект, редакция и дата', { exact: true }).click()
  await auditor.getByRole('button', { name: 'Комплект поиска', exact: true }).click()
  await auditor.getByRole('dialog').getByRole('button', { name: 'Учебный комплект до' }).click()
  await auditor
    .getByLabel('Запрос по содержанию')
    .fill('учёт поступивших документов и проверка версий')
  await auditor.getByRole('button', { name: 'Найти', exact: true }).click()
  await expect(auditor.locator('.search-hit').first()).toContainText('ведёт реестр документов')
  await expect(auditor.getByText(/Режим выдачи: слова и смысл/)).toBeVisible()
  await capture(auditor, '14-hybrid-search-1440.png')
  await auditor.setViewportSize({ width: 390, height: 844 })
  await capture(auditor, '14-hybrid-search-390.png')
  await auditor.setViewportSize({ width: 1440, height: 1000 })
  await auditor
    .locator('.search-hit')
    .first()
    .getByRole('button', { name: 'Открыть источник', exact: true })
    .click()
  await expect(auditor.locator('.source-dialog mark')).toContainText('ведёт реестр документов')
  await auditor.getByRole('button', { name: 'Закрыть источник' }).click()
  const persisted = await json(auditor, `/comparisons/${savedRun.id}/findings?category=conflict`)
  expect(persisted.items[0].review.version).toBe(1)
  for (const path of [
    `/corpora/${beforeCorpus.id}`,
    `/comparisons/${savedRun.id}`,
    `/comparisons/${savedRun.id}/findings`,
    `/comparisons/${savedRun.id}/findings/${persisted.items[0].id}/reviews`,
    `/analysis-jobs/${savedRun.job.id}`,
    `/search?q=documents&corpus_id=${beforeCorpus.id}`,
  ])
    expect((await restricted.request.get('/api/v1' + path)).status()).toBe(404)
  expect((await json(restricted, '/corpora')).total).toBe(0)
  expect((await json(restricted, '/comparisons')).total).toBe(0)
  const restrictedSearch = await json(
    restricted,
    '/search?q=' + encodeURIComponent('реестр документов'),
  )
  expect(
    restrictedSearch.items.every(
      (h: { department_id: string }) => h.department_id !== itDepartment.id,
    ),
  ).toBe(true)
  // Audit columns resolve the actor from the real registered event and preserve its object ID.
  await navigate(auditor, 'Журнал действий')
  for (const heading of ['Время', 'Автор', 'Действие', 'Объект', 'Результат']) {
    await expect(auditor.getByRole('columnheader', { name: heading, exact: true })).toBeVisible()
  }
  await auditor.getByLabel('Поиск по типу события').fill('personnel.terminate')
  const terminationLog = await json(auditor, '/audit?category=business&q=personnel.terminate')
  expect(terminationLog.total).toBe(1)
  const hrUser = await json(page, '/auth/me')
  expect(terminationLog.items[0].actor_display_name).toBe(hrUser.display_name)
  await expect(auditor.locator('.audit-table tbody tr')).toHaveCount(1)
  const auditRow = auditor.locator('.audit-table tbody tr').first()
  await expect(auditRow.getByRole('cell', { name: hrUser.display_name, exact: true })).toBeVisible()
  await expect(auditRow).toContainText('Назначение прекращено')
  await expect(
    auditRow.getByRole('cell', { name: terminationLog.items[0].object_id, exact: true }),
  ).toBeVisible()
  await expect(auditRow.getByRole('cell', { name: 'Зафиксировано', exact: true })).toBeVisible()
  await capture(auditor, '19-audit-1440.png')
  expect(errors).toEqual([])
  await auditorContext.close()
  await restrictedContext.close()
})
