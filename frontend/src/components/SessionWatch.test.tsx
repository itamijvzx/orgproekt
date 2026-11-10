import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import type { User } from '@/lib/types'
import { SessionWatch } from './SessionWatch'

const api = vi.hoisted(() => ({ get: vi.fn(), active: vi.fn() }))
vi.mock('@/lib/client', () => ({
  client: { GET: api.get },
  result: async (request: Promise<User>) => request,
  userRecentlyActive: api.active,
  errorMessage: (error: Error) => error.message,
  ApiError: class extends Error {},
}))

function session() {
  return {
    session_id: 'test-session',
    session_idle_seconds: 30 * 60,
    idle_expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    session_expires_at: new Date(Date.now() + 8 * 60 * 60_000).toISOString(),
  } as User
}

function Form({ initial }: { initial: User }) {
  const [user, setUser] = useState(initial)
  return (
    <>
      <textarea aria-label="Черновик" defaultValue="Несохранённый текст предложения" />
      <SessionWatch user={user} onRenewed={setUser} />
    </>
  )
}

afterEach(() => {
  vi.useRealTimers()
  api.get.mockReset()
  api.active.mockReset()
})

it('warns before idle expiry and keeps an open draft after renewal', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-10T10:00:00Z'))
  api.active.mockReturnValue(false)
  api.get.mockImplementation(async () => session())
  render(<Form initial={session()} />)
  await act(async () => vi.advanceTimersByTimeAsync(25 * 60_000))
  expect(screen.getByText(/Сеанс завершится примерно через 5 мин/)).toBeInTheDocument()

  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Продолжить сеанс' })))
  expect(api.get).toHaveBeenCalledWith('/api/v1/auth/me')
  expect(screen.queryByText(/Сеанс завершится/)).not.toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Черновик' })).toHaveValue(
    'Несохранённый текст предложения',
  )
})

it('renews a long active editing session without interrupting the form', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-10T10:00:00Z'))
  api.active.mockReturnValue(true)
  api.get.mockImplementation(async () => session())
  render(<Form initial={session()} />)
  await act(async () => vi.advanceTimersByTimeAsync(31 * 60_000))
  expect(api.get.mock.calls.length).toBeGreaterThanOrEqual(30)
  expect(screen.queryByText(/Сеанс завершится/)).not.toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Черновик' })).toHaveValue(
    'Несохранённый текст предложения',
  )
})
