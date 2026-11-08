import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AccessWorkspace } from './AccessWorkspace'
import type { User } from '@/lib/types'

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/client', async (original) => ({
  ...(await original<typeof import('@/lib/client')>()),
  client: { GET: requests.get, POST: requests.post },
}))
const reply = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) })
const user: User = {
  id: 'admin',
  username: 'access',
  display_name: 'Учебный администратор',
  roles: [{ role: 'access_admin', department_id: null }],
  csrf_token: '',
  session_id: 'session',
  reauthenticated_at: '2026-10-30T00:00:00Z',
  mode: 'test',
  timezone: 'Asia/Almaty',
}

beforeEach(() => {
  requests.get.mockReset().mockImplementation((path: string) =>
    Promise.resolve(
      reply(
        path === '/api/v1/access/options'
          ? {
              is_admin: true,
              can_approve: false,
              local_delivery: true,
              departments: [{ id: 'it', label: 'ИТ' }],
            }
          : { items: [], total: 0, offset: 0, limit: 20 },
      ),
    ),
  )
  requests.post.mockReset()
})

describe('Account access commands', () => {
  it('preserves invite fields and reuses command identity after a conflict or retry', async () => {
    requests.post.mockResolvedValue({
      error: { message: 'Сведения изменились', code: 'conflict' },
      response: new Response(null, { status: 409 }),
    })
    render(<AccessWorkspace user={user} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Пригласить пользователя' }))
    fireEvent.change(screen.getByLabelText('Логин нового пользователя', { exact: true }), {
      target: { value: 'new_person' },
    })
    fireEvent.change(screen.getByLabelText('Имя пользователя', { exact: true }), {
      target: { value: 'Елена Учебная' },
    })
    fireEvent.change(screen.getByLabelText('Основание приглашения', { exact: true }), {
      target: { value: 'Учебное основание приглашения' },
    })
    const save = screen.getByRole('button', { name: 'Создать приглашение' })
    fireEvent.click(save)
    await screen.findByRole('alert')
    expect(screen.getByLabelText('Основание приглашения', { exact: true })).toHaveValue(
      'Учебное основание приглашения',
    )
    const key = requests.post.mock.calls[0][1].params.header['idempotency-key']
    fireEvent.click(save)
    await waitFor(() => expect(requests.post).toHaveBeenCalledTimes(2))
    expect(requests.post.mock.calls[1][1].params.header['idempotency-key']).toBe(key)
    fireEvent.change(screen.getByLabelText('Основание приглашения', { exact: true }), {
      target: { value: 'Исправленное основание приглашения' },
    })
    await waitFor(() => expect(save).not.toBeDisabled())
    fireEvent.click(save)
    await waitFor(() => expect(requests.post).toHaveBeenCalledTimes(3))
    expect(requests.post.mock.calls[2][1].params.header['idempotency-key']).not.toBe(key)
  })

  it('retries an initial directory capability failure and does not invent empty success', async () => {
    let attempts = 0
    requests.get.mockImplementation((path: string) => {
      if (path === '/api/v1/access/options' && ++attempts === 1)
        return Promise.resolve({
          error: { message: 'Нет связи с сервером' },
          response: new Response(null, { status: 503 }),
        })
      return Promise.resolve(
        reply(
          path === '/api/v1/access/options'
            ? { is_admin: true, can_approve: false, local_delivery: true, departments: [] }
            : { items: [], total: 0, offset: 0, limit: 20 },
        ),
      )
    })
    render(<AccessWorkspace user={user} />)
    await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: 'Пригласить пользователя' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
    await screen.findByRole('heading', { name: 'Пользователи и права' })
    expect(attempts).toBe(2)
  })
})
