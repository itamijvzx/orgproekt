import { test, expect, type Page } from '@playwright/test'
import { createHash, randomUUID } from 'node:crypto'
import { signIn } from './report-helpers'

async function openReports(page: Page) {
  const menu = page.getByRole('button', { name: 'Открыть меню', exact: true })
  if (await menu.isVisible()) {
    await menu.click()
    await page.getByRole('dialog').getByRole('button', { name: 'Отчёты', exact: true }).click()
  } else {
    await page.getByRole('button', { name: 'Изменения', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Отчёты', exact: true }).click()
  }
  await expect(
    page.getByRole('heading', { name: 'Заключения и выписки', exact: true }),
  ).toBeVisible()
}
async function post(page: Page, path: string, data: unknown) {
  const me = await (await page.request.get('/api/v1/auth/me')).json()
  const response = await page.request.post('/api/v1' + path, {
    data,
    headers: {
      'X-CSRF-Token': me.csrf_token,
      'Idempotency-Key': randomUUID(),
      Origin: new URL(page.url()).origin,
    },
  })
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}
async function screenshot(page: Page, file: string) {
  await expect
    .poll(() => page.locator('.page-content').evaluate((el) => getComputedStyle(el).opacity))
    .toBe('1')
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: '/screenshots/' + file, fullPage: true, animations: 'disabled' })
}

test('Этап 4: подготовка → доработка → проверка → выпуск → файлы → область доступа', async ({
  page,
  browser,
}) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await signIn(page, 'auditor')
  const options = await (await page.request.get('/api/v1/reports/options')).json()
  const department = options.departments.find(
    (d: { label: string }) => d.label === 'Информационные технологии',
  )
  const context = await (
    await page.request.get('/api/v1/changes/context?department_id=' + department.id)
  ).json()
  const source = context.sources.find((s: { label: string }) => s.label.includes('Инструкция'))
  expect(source).toBeTruthy()
  const proposal = await post(page, '/changes', {
    title: 'Проверка порядка резервного копирования · отчёт E2E',
    effective_date: '2026-10-30',
    sections: [
      {
        department_id: department.id,
        title: 'Учёт результатов восстановления',
        rationale: 'Уточнить форму подтверждения результата проверки.',
        items: [
          {
            key: 'report_item',
            kind: 'document',
            before: 'Результаты проверки фиксируются в рабочем журнале.',
            after: 'Журнал содержит ссылку на проверенный комплект резервных копий.',
            source_anchor_ids: [source.id],
          },
        ],
      },
    ],
  })
  await post(page, `/changes/${proposal.id}/notes`, {
    expected_version: proposal.version,
    department_id: department.id,
    text: 'PRIVATE_REPORT_E2E_DO_NOT_SHARE',
  })
  await openReports(page)
  await page.getByRole('button', { name: 'Подготовить отчёт', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Предложение', { exact: true }).selectOption(proposal.id)
  await dialog
    .getByLabel('Рецензент аудита', { exact: true })
    .selectOption({ label: 'Павел Учебный — рецензент' })
  await dialog.getByRole('checkbox', { name: /Лев Соснов/ }).check()
  await dialog.getByRole('button', { name: 'Создать черновик отчёта', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  const title = `Заключение: порядок проверки резервных копий · E2E ${Date.now().toString().slice(-6)}`
  await page.getByLabel('Название отчёта', { exact: true }).fill(title)
  const values: Record<string, string> = {
    'Цель проверки': 'Оценить документальное закрепление проверки восстановления резервных копий.',
    'Область проверки':
      'Подразделение «Информационные технологии»; документирование результатов восстановления.',
    'Период проверки': '20–30 октября 2026 года',
    Критерии: 'Каждая проверка должна иметь дату, результат и ссылку на проверенный комплект.',
    'Метод проверки':
      'Сопоставлены сохранённая инструкция и предлагаемая редакция. Вывод проверяется рецензентом вручную; Gemini не использовался.',
    Ограничения: 'Использованы синтетические данные. Фактическое восстановление не тестировалось.',
    'Замечания и доказательства':
      'В прежней формулировке отсутствует явное требование ссылки на комплект. Основание приведено ниже в сохранённой редакции инструкции.',
    'Значимость замечаний': 'Средняя: без ссылки затруднено повторное подтверждение результата.',
    Рекомендации: 'Дополнить форму журнала ссылкой на комплект и проверять полноту записей.',
    'Мероприятия ответственные и сроки':
      'Руководитель ИТ — подготовить форму журнала до 15 ноября 2026 года; аудитор — проверить документальное подтверждение до 20 ноября 2026 года.',
    'Профессиональный вывод':
      'Документирование требует уточнения. Исполнение рекомендации на дату отчёта не подтверждено; открытое замечание сохраняется.',
  }
  for (const [label, value] of Object.entries(values))
    await page.getByLabel(label, { exact: true }).fill(value)
  await page.getByRole('button', { name: 'Сохранить редакцию', exact: true }).click()
  await expect(page.getByText('Есть несохранённый текст', { exact: true })).not.toBeVisible()
  await screenshot(page, '41-report-draft-1440.png')
  await page.getByRole('button', { name: 'Направить на проверку', exact: true }).click()
  await expect(page.locator('.report-status')).toContainText('На проверке')
  const report = (
    await (await page.request.get('/api/v1/reports?q=' + encodeURIComponent(title))).json()
  ).items[0]
  expect(await (await page.request.get(`/api/v1/reports/${report.id}`)).text()).not.toContain(
    'PRIVATE_REPORT_E2E_DO_NOT_SHARE',
  )

  const reviewerContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'ru-RU',
  })
  const reviewer = await reviewerContext.newPage()
  reviewer.on('pageerror', (e) => errors.push(e.message))
  await signIn(reviewer, 'stage3_reviewer')
  await openReports(reviewer)
  await reviewer.getByRole('button', { name: title, exact: true }).click()
  await reviewer.getByLabel('Решение рецензента', { exact: true }).selectOption('return')
  await reviewer
    .getByLabel('Комментарий к действию', { exact: true })
    .fill('Укажите ограничение: операционные доказательства исполнения ещё не представлены.')
  await reviewer.getByRole('button', { name: 'Сохранить проверку', exact: true }).click()
  await expect(reviewer.locator('.report-status')).toContainText('На доработке')
  await page.getByRole('button', { name: 'Обновить сведения', exact: true }).click()
  await page
    .getByLabel('Ограничения', { exact: true })
    .fill(values['Ограничения'] + ' Операционные доказательства исполнения ещё не представлены.')
  await page.getByRole('button', { name: 'Сохранить редакцию', exact: true }).click()
  await expect(page.locator('.report-status')).toContainText('Черновик')
  await page.getByRole('button', { name: 'Направить на проверку', exact: true }).click()
  await expect(page.locator('.report-status')).toContainText('На проверке')
  await reviewer.getByRole('button', { name: 'Обновить сведения', exact: true }).click()
  await reviewer.getByLabel('Решение рецензента', { exact: true }).selectOption('accept')
  await reviewer
    .getByLabel('Комментарий к действию', { exact: true })
    .fill('Источники, вывод и адресаты проверены. Открытое замечание отражено явно.')
  await reviewer.getByRole('checkbox', { name: /Проверены источники/ }).check()
  await reviewer.getByRole('button', { name: 'Сохранить проверку', exact: true }).click()
  await expect(reviewer.locator('.report-status')).toContainText('Проверен рецензентом')
  await reviewer
    .getByLabel('Комментарий к действию', { exact: true })
    .fill('Выпустить проверенную редакцию для разрешённых адресатов.')
  await reviewer.getByRole('button', { name: 'Выпустить отчёт', exact: true }).click()
  await expect(reviewer.locator('.report-status')).toContainText('Выпущен')
  await screenshot(reviewer, '42-report-released-1440.png')
  const release = await (await reviewer.request.get(`/api/v1/reports/${report.id}`)).json()
  for (const format of ['pdf', 'docx']) {
    const file = reviewer.waitForEvent('download')
    await reviewer
      .getByRole('button', { name: 'Скачать ' + format.toUpperCase(), exact: true })
      .click()
    await (await file).saveAs('/screenshots/report-release.' + format)
    const bytes = await (
      await reviewer.request.get(`/api/v1/reports/${report.id}/export/${format}`)
    ).body()
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(
      release.release[format + '_sha256'],
    )
  }
  await reviewer.reload()
  await openReports(reviewer)
  await reviewer.getByRole('button', { name: title, exact: true }).click()
  await expect(reviewer.getByRole('button', { name: 'Выпустить отчёт', exact: true })).toHaveCount(
    0,
  )

  const readerContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    locale: 'ru-RU',
  })
  const reader = await readerContext.newPage()
  reader.on('pageerror', (e) => errors.push(e.message))
  await signIn(reader, 'it')
  await openReports(reader)
  await reader.getByRole('button', { name: title, exact: true }).click()
  await expect(reader.getByRole('button', { name: 'Скачать PDF', exact: true })).toBeVisible()
  await screenshot(reader, '43-report-reader-390.png')
  expect((await reader.request.get(`/api/v1/reports/${report.id}/revisions/1`)).status()).toBe(404)
  const foreignContext = await browser.newContext()
  const foreign = await foreignContext.newPage()
  await signIn(foreign, 'finance')
  for (const suffix of ['', '/export/pdf', '/export/docx', '/execution', '/revisions/1'])
    expect((await foreign.request.get(`/api/v1/reports/${report.id}${suffix}`)).status()).toBe(404)
  expect(
    (await (await foreign.request.get('/api/v1/reports?q=' + encodeURIComponent(title))).json())
      .total,
  ).toBe(0)

  await page.getByRole('button', { name: 'Обновить сведения', exact: true }).click()
  await page.getByRole('button', { name: 'Подготовить исправленный выпуск', exact: true }).click()
  await page
    .getByRole('dialog')
    .getByRole('checkbox', { name: /Лев Соснов/ })
    .check()
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Создать черновик отчёта', exact: true })
    .click()
  await expect(page.locator('.report-status')).toContainText('Черновик')
  expect(
    await (await reader.request.get(`/api/v1/reports/${report.id}/export/pdf`)).body(),
  ).toEqual(await (await reviewer.request.get(`/api/v1/reports/${report.id}/export/pdf`)).body())
  expect(errors).toEqual([])
  await reviewerContext.close()
  await readerContext.close()
  await foreignContext.close()
})
