import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkOverview } from './WorkOverview'
import { useUI } from '@/lib/store'
import type { FunctionItem, Snapshot, User } from '@/lib/types'

const requests = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/client')>()),
  client: { GET: requests.get },
}))

const first: FunctionItem = {
  id: 'function-a',
  code: 'A-1',
  title: 'Учёт документов',
  department_id: 'dept-a',
  source_anchor_id: 'source-a',
  lifecycle: 'active',
  status: 'uncovered',
  performers: [],
}
const second: FunctionItem = {
  ...first,
  id: 'function-b',
  code: 'A-2',
  title: 'Сверка реестра',
  source_anchor_id: 'source-b',
  status: 'unknown',
}
const data: Snapshot = {
  as_of: '2026-10-29',
  organization: 'OrgProekt',
  synthetic: true,
  departments: [
    {
      id: 'dept-a',
      name: 'Учебный отдел',
      code: 'A',
      parent_id: null,
      registry_complete: true,
      employee_count: 0,
      position_count: 0,
      seat_count: 0,
      function_count: 2,
    },
  ],
  positions: [],
  seats: [],
  functions: [first, second],
  assignments: [],
}
const user: User = {
  id: 'auditor',
  username: 'auditor',
  display_name: 'Учебный аудитор',
  roles: [{ role: 'auditor', department_id: 'dept-a' }],
  csrf_token: '',
  session_id: 'session',
  reauthenticated_at: '2026-10-29T00:00:00Z',
  mode: 'test',
  timezone: 'Asia/Almaty',
}
const reply = (value: unknown) => ({ data: value, response: new Response(null, { status: 200 }) })
const overview = (items = [first, second]) => ({
  as_of: data.as_of,
  organization: data.organization,
  attention: { items, total: items.length, limit: 6, offset: 0 },
  counts: { employees: 0, departments: 1, uncovered: 1, unknown: 1, unread_notifications: 0 },
  unavailable: ['tasks', 'reviews'],
})
type RequestOptions = {
  params: { path?: { function_id?: string; anchor_id?: string }; query?: Record<string, unknown> }
  signal: AbortSignal
}
const load = (path: string, options: RequestOptions) => {
  if (path === '/api/v1/overview') return Promise.resolve(reply(overview()))
  if (path === '/api/v1/audit')
    return Promise.resolve(reply({ items: [], total: 0, offset: 0, limit: 3 }))
  if (path === '/api/v1/functions/{function_id}') {
    const item = options.params.path?.function_id === first.id ? first : second
    return Promise.resolve(
      reply({ ...item, periods: [{ start: '2026-01-01', end: null, status: item.status }] }),
    )
  }
  if (path === '/api/v1/sources/{anchor_id}') {
    const isFirst = options.params.path?.anchor_id === first.source_anchor_id
    return Promise.resolve(
      reply({
        id: isFirst ? 'source-a' : 'source-b',
        title: 'Учебное положение',
        revision: '1',
        locator: 'п. 1',
        quote: isFirst ? 'Точная цитата А.' : 'Точная цитата Б.',
        synthetic: true,
      }),
    )
  }
  throw new Error(`Unexpected test request: ${path}`)
}

beforeEach(() => {
  useUI.getState().reset()
  useUI.getState().set({ asOf: data.as_of })
  requests.get.mockReset().mockImplementation(load)
})

function renderOverview() {
  const onSource = vi.fn(),
    onFunction = vi.fn(),
    onNavigate = vi.fn()
  const view = render(
    <WorkOverview
      data={data}
      user={user}
      revision={0}
      onSource={onSource}
      onFunction={onFunction}
      onNavigate={onNavigate}
    />,
  )
  return { ...view, onSource, onFunction, onNavigate }
}

describe('Working overview evidence and selection', () => {
  it('clears previous details and source while another question loads; opens the matching source', async () => {
    let finishSecond!: (value: ReturnType<typeof reply>) => void
    requests.get.mockImplementation((path: string, options: RequestOptions) => {
      if (
        path === '/api/v1/functions/{function_id}' &&
        options.params.path?.function_id === second.id
      )
        return new Promise((resolve) => {
          finishSecond = resolve
        })
      return load(path, options)
    })
    const { onSource } = renderOverview()
    expect(await screen.findByText('Точная цитата А.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: `Открыть вопрос: ${second.title}` }))
    const detail = screen.getByRole('region', { name: 'Подробности выбранного вопроса' })
    expect(within(detail).queryByRole('heading', { name: first.title })).not.toBeInTheDocument()
    expect(within(detail).queryByText('Точная цитата А.')).not.toBeInTheDocument()
    expect(within(detail).getByRole('status')).toBeVisible()
    await act(async () => {
      finishSecond(reply({ ...second, periods: [] }))
    })
    expect(await within(detail).findByRole('heading', { name: second.title })).toBeVisible()
    expect(await within(detail).findByText('Точная цитата Б.')).toBeVisible()
    fireEvent.click(within(detail).getByRole('button', { name: 'Открыть источник' }))
    expect(onSource).toHaveBeenCalledWith('source-b')
  })

  it('removes the selected panel during server filtering, handles empty results, and sends the new date', async () => {
    let finishFilter!: (value: ReturnType<typeof reply>) => void
    requests.get.mockImplementation((path: string, options: RequestOptions) => {
      if (path === '/api/v1/overview' && options.params.query?.q === 'Нет совпадений')
        return new Promise((resolve) => {
          finishFilter = resolve
        })
      return load(path, options)
    })
    const view = renderOverview()
    await screen.findByText('Точная цитата А.')
    fireEvent.change(screen.getByLabelText('Найти вопрос'), { target: { value: 'Нет совпадений' } })
    expect(
      screen.queryByRole('region', { name: 'Подробности выбранного вопроса' }),
    ).not.toBeInTheDocument()
    await act(async () => {
      finishFilter(reply(overview([])))
    })
    expect(
      await screen.findByRole('heading', { name: 'По этим условиям вопросов нет' }),
    ).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }))
    await screen.findByText('Точная цитата А.')
    act(() => {
      useUI.getState().set({ asOf: '2026-10-30' })
    })
    view.rerender(
      <WorkOverview
        data={{ ...data, as_of: '2026-10-30' }}
        user={user}
        revision={0}
        onSource={view.onSource}
        onFunction={view.onFunction}
        onNavigate={view.onNavigate}
      />,
    )
    await waitFor(() =>
      expect(requests.get).toHaveBeenCalledWith(
        '/api/v1/functions/{function_id}',
        expect.objectContaining({
          params: { path: { function_id: first.id }, query: { as_of: '2026-10-30' } },
        }),
      ),
    )
  })

  it('shows an access denial instead of leaving previously loaded evidence visible', async () => {
    requests.get.mockImplementation((path: string, options: RequestOptions) => {
      if (
        path === '/api/v1/functions/{function_id}' &&
        options.params.path?.function_id === second.id
      )
        return Promise.resolve({
          error: { message: 'Объект недоступен или не существует' },
          response: new Response(null, { status: 404 }),
        })
      return load(path, options)
    })
    renderOverview()
    await screen.findByText('Точная цитата А.')
    fireEvent.click(screen.getByRole('button', { name: `Открыть вопрос: ${second.title}` }))
    const detail = screen.getByRole('region', { name: 'Подробности выбранного вопроса' })
    expect(await within(detail).findByRole('alert')).toHaveTextContent(
      'Объект недоступен или не существует',
    )
    expect(within(detail).queryByText('Точная цитата А.')).not.toBeInTheDocument()
    expect(
      within(detail).queryByRole('button', { name: 'Открыть источник' }),
    ).not.toBeInTheDocument()
  })
})
