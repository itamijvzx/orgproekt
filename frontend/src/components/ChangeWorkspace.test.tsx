import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { components } from '@/lib/schema.generated'
import type { Snapshot, User } from '@/lib/types'
import { ChangeWorkspace } from './ChangeWorkspace'

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/client')>()),
  client: { GET: requests.get, POST: requests.post },
}))
const reply = (value: unknown) => ({ data: value, response: new Response(null, { status: 200 }) })
const department = { id: 'dept-a', label: 'Учебный отдел' }
const detail: components['schemas']['ProposalDetail'] = {
  id: 'proposal-a',
  title: 'Уточнение ответственности',
  state: 'in_review',
  version: 1,
  current_version: 1,
  effective_date: '2026-10-29',
  created_at: '2026-10-29T00:00:00Z',
  updated_at: '2026-10-29T00:00:00Z',
  departments: [department],
  partial_visibility: false,
  overdue: false,
  owner_name: 'Учебный аудитор',
  permissions: {
    revise: false,
    send: false,
    message: true,
    private_note: false,
    decide: false,
    verify: false,
    remind: false,
    cancel: false,
  },
  limitations: [],
  entries: [],
  process_entries: [],
  history_total: 0,
  history_offset: 0,
  history_limit: 100,
  versions_total: 1,
  versions_offset: 0,
  versions_limit: 20,
  versions: [
    {
      id: 'version-a',
      number: 1,
      title: 'Уточнение ответственности',
      effective_date: '2026-10-29',
      change_reason: '',
      created_at: '2026-10-29T00:00:00Z',
      sections: [
        {
          department_id: department.id,
          department_name: department.label,
          title: 'Раздел отдела',
          rationale: 'Документальное уточнение',
          can_review: true,
          can_execute: false,
          items: [
            {
              key: 'item-a',
              kind: 'responsibility',
              before: 'До',
              after: 'После',
              source_anchor_ids: [],
              qualification_source_ids: [],
              qualification_basis: '',
              requirements: '',
              impacts: [],
            },
          ],
        },
      ],
    },
  ],
}
const user: User = {
  id: 'representative',
  username: 'representative',
  display_name: 'Представитель отдела',
  roles: [{ role: 'department', department_id: department.id }],
  csrf_token: '',
  session_id: 'session',
  reauthenticated_at: '2026-10-29T00:00:00Z',
  mode: 'test',
  timezone: 'Asia/Almaty',
}
const snapshot: Snapshot = {
  organization: 'OrgProekt',
  as_of: '2026-10-29',
  synthetic: true,
  departments: [],
  positions: [],
  seats: [],
  functions: [],
  assignments: [],
}
const context = {
  sources: [],
  functions: [],
  positions: [],
  seats: [],
  employees: [],
  personnel_events: [],
}
const load = (path: string) => {
  if (path === '/api/v1/changes/options')
    return Promise.resolve(
      reply({
        departments: [department],
        author_departments: [],
        participants: [],
        can_create: false,
      }),
    )
  if (path === '/api/v1/changes')
    return Promise.resolve(reply({ items: [], total: 0, offset: 0, limit: 15 }))
  if (path === '/api/v1/changes/{proposal_id}') return Promise.resolve(reply(detail))
  if (path === '/api/v1/changes/context') return Promise.resolve(reply(context))
  throw new Error(`Unexpected test request: ${path}`)
}
function selectTab(name: string) {
  const tab = screen.getByRole('tab', { name })
  fireEvent.mouseDown(tab, { button: 0, ctrlKey: false })
  fireEvent.click(tab)
}
function openWorkspace() {
  return render(
    <ChangeWorkspace
      data={snapshot}
      user={user}
      revision={0}
      initialProposalId={detail.id}
      onSource={vi.fn()}
    />,
  )
}
beforeEach(() => {
  requests.get.mockReset().mockImplementation(load)
  requests.post.mockReset().mockResolvedValue(reply(detail))
})

describe('Change workspace draft preservation', () => {
  it('removes unsent data from a department when a case becomes partially accessible', async () => {
    const otherDepartment = { id: 'dept-b', label: 'Второй учебный отдел' }
    const secondSection = {
      ...detail.versions[0].sections[0],
      department_id: otherDepartment.id,
      department_name: otherDepartment.label,
      title: 'Второй раздел',
    }
    const fullCase = {
      ...detail,
      departments: [department, otherDepartment],
      versions: [
        { ...detail.versions[0], sections: [...detail.versions[0].sections, secondSection] },
      ],
    }
    const partialCase = {
      ...detail,
      version: 2,
      partial_visibility: true,
      departments: [otherDepartment],
      versions: [{ ...detail.versions[0], sections: [secondSection] }],
    }
    let revoked = false
    requests.get.mockImplementation((path: string) =>
      path === '/api/v1/changes/{proposal_id}'
        ? Promise.resolve(reply(revoked ? partialCase : fullCase))
        : load(path),
    )
    openWorkspace()
    await screen.findByRole('heading', { name: detail.title })
    selectTab('Согласование')
    fireEvent.change(await screen.findByRole('textbox', { name: 'Обоснование ответа' }), {
      target: { value: 'Закрытый неотправленный текст первого отдела' },
    })
    selectTab('Обсуждение')
    fireEvent.change(await screen.findByRole('textbox', { name: 'Текст сообщения' }), {
      target: { value: 'Уточнение до пересмотра прав' },
    })
    revoked = true
    fireEvent.click(screen.getByRole('button', { name: 'Отправить сообщение' }))
    await waitFor(() =>
      expect(screen.getByRole('combobox', { name: 'Раздел обсуждения' })).toHaveValue(
        otherDepartment.id,
      ),
    )
    expect(
      screen.queryByDisplayValue('Закрытый неотправленный текст первого отдела'),
    ).not.toBeInTheDocument()
    selectTab('Согласование')
    expect(screen.getByRole('textbox', { name: 'Обоснование ответа' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Подразделение для ответа' })).toHaveValue(
      otherDepartment.id,
    )
  })

  it('keeps an unsent review while context loads, tabs change and the same case refreshes', async () => {
    const pendingContext: Array<(value: ReturnType<typeof reply>) => void> = []
    let delayRefresh = false
    let finishRefresh: ((value: ReturnType<typeof reply>) => void) | undefined
    requests.get.mockImplementation((path: string) => {
      if (path === '/api/v1/changes/context')
        return new Promise((resolve) => pendingContext.push(resolve))
      if (path === '/api/v1/changes/{proposal_id}' && delayRefresh)
        return new Promise((resolve) => {
          finishRefresh = resolve
        })
      return load(path)
    })
    openWorkspace()
    await screen.findByRole('heading', { name: detail.title })
    selectTab('Согласование')
    const review = await screen.findByRole('textbox', { name: 'Обоснование ответа' })
    fireEvent.change(review, { target: { value: 'Проверены полномочия отдела.' } })
    await act(async () => {
      pendingContext.splice(0).forEach((resolve) => resolve(reply(context)))
    })
    expect(review).toHaveValue('Проверены полномочия отдела.')
    selectTab('Обсуждение')
    fireEvent.change(await screen.findByRole('textbox', { name: 'Текст сообщения' }), {
      target: { value: 'Уточняющий вопрос.' },
    })
    delayRefresh = true
    fireEvent.click(screen.getByRole('button', { name: 'Отправить сообщение' }))
    await waitFor(() => expect(finishRefresh).toBeTypeOf('function'))
    selectTab('Согласование')
    const preserved = screen.getByRole('textbox', { name: 'Обоснование ответа' })
    expect(preserved).toBe(review)
    expect(preserved.closest('[inert]')).toBeNull()
    fireEvent.change(preserved, {
      target: { value: 'Проверены полномочия и документальные основания.' },
    })
    delayRefresh = false
    await act(async () => {
      finishRefresh!(reply({ ...detail, version: 2 }))
    })
    expect(screen.getByRole('textbox', { name: 'Обоснование ответа' })).toBe(review)
    expect(review).toHaveValue('Проверены полномочия и документальные основания.')
    fireEvent.click(screen.getByRole('button', { name: 'Отправить ответ подразделения' }))
    await waitFor(() =>
      expect(requests.post).toHaveBeenCalledWith(
        '/api/v1/changes/{proposal_id}/reviews',
        expect.objectContaining({
          body: expect.objectContaining({
            expected_version: 2,
            text: 'Проверены полномочия и документальные основания.',
          }),
        }),
      ),
    )
  })

  it('clears cached case data and unsent forms when refreshing access is denied', async () => {
    let denyAccess = false
    requests.get.mockImplementation((path: string) =>
      path === '/api/v1/changes/{proposal_id}' && denyAccess
        ? Promise.resolve({
            error: { message: 'Доступ к предложению закрыт' },
            response: new Response(null, { status: 403 }),
          })
        : load(path),
    )
    openWorkspace()
    await screen.findByRole('heading', { name: detail.title })
    selectTab('Согласование')
    fireEvent.change(await screen.findByRole('textbox', { name: 'Обоснование ответа' }), {
      target: { value: 'Неотправленный ответ' },
    })
    selectTab('Обсуждение')
    fireEvent.change(await screen.findByRole('textbox', { name: 'Текст сообщения' }), {
      target: { value: 'Вопрос' },
    })
    denyAccess = true
    fireEvent.click(screen.getByRole('button', { name: 'Отправить сообщение' }))
    expect(await screen.findByText('Доступ к предложению закрыт')).toBeVisible()
    expect(screen.queryByRole('heading', { name: detail.title })).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue('Неотправленный ответ')).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('alert')).getByRole('button', { name: 'Повторить' }),
    ).toBeVisible()
  })
})
