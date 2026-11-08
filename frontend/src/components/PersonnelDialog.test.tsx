import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PersonnelDialog } from './PersonnelDialog'
import type { Snapshot } from '@/lib/types'
import type { components } from '@/lib/schema.generated'

const requests = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/api', () => ({ localToday: () => '2026-10-28' }))
vi.mock('@/lib/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/client')>()),
  client: { GET: requests.get, POST: requests.post },
}))

const seat = {
  id: 'seat-1',
  position_id: 'position-1',
  code: 'IT-01',
  version: 4,
  appointments: [],
}
const data: Snapshot = {
  as_of: '2026-10-28',
  organization: 'Синтетическая',
  synthetic: true,
  departments: [
    {
      id: 'department-1',
      name: 'ИТ',
      code: 'IT',
      parent_id: null,
      registry_complete: true,
      employee_count: 0,
      position_count: 1,
      seat_count: 1,
      function_count: 0,
    },
  ],
  positions: [{ id: 'position-1', title: 'Инженер', department_id: 'department-1' }],
  seats: [seat],
  functions: [],
  assignments: [],
}
const conflictMessage = 'Запись уже изменена. Обновите данные перед повтором.'
const conflict = () => ({
  error: {
    code: 'conflict',
    message: conflictMessage,
    detail: conflictMessage,
    field_errors: [],
    correlation_id: 'request-1',
  } satisfies components['schemas']['ErrorResponse'],
  response: new Response(null, { status: 409 }),
})

async function fillAppointment() {
  const saved = vi.fn(),
    close = vi.fn()
  render(
    <PersonnelDialog
      target={{ seat, action: 'appoint' }}
      data={data}
      onClose={close}
      onSaved={saved}
    />,
  )
  await screen.findByRole('option', { name: 'Синтетический сотрудник' })
  fireEvent.change(screen.getByLabelText('Сотрудник'), { target: { value: 'employee-1' } })
  fireEvent.change(screen.getByLabelText('Название и номер документа'), {
    target: { value: 'Учебный приказ № 12' },
  })
  fireEvent.change(screen.getByLabelText('Точная выдержка'), {
    target: { value: 'Назначить синтетического сотрудника с указанной даты.' },
  })
  const submit = screen.getByRole('button', { name: 'Зарегистрировать событие' })
  expect(submit).toBeDisabled()
  fireEvent.click(screen.getByLabelText(/Подтверждаю полномочия/))
  return { saved, close, submit }
}

beforeEach(() => {
  requests.get.mockReset().mockResolvedValue({
    data: [{ id: 'employee-1', display_name: 'Синтетический сотрудник' }],
    response: new Response(null, { status: 200 }),
  })
  requests.post.mockReset().mockImplementation(async () => conflict())
})

describe('Official personnel confirmation', () => {
  it('keeps the form and evidence after a 409 response and does not claim success', async () => {
    const { saved, close, submit } = await fillAppointment()
    fireEvent.click(submit)
    expect(await screen.findByRole('alert')).toHaveTextContent(conflictMessage)
    expect(requests.get).toHaveBeenCalledWith(
      '/api/v1/employees',
      expect.objectContaining({
        params: { query: { department_id: 'department-1' } },
        signal: expect.any(AbortSignal),
      }),
    )
    expect(requests.post).toHaveBeenCalledWith(
      '/api/v1/personnel/events',
      expect.objectContaining({
        body: expect.objectContaining({
          action: 'appoint',
          seat_id: 'seat-1',
          employee_id: 'employee-1',
          expected_version: 4,
          basis: expect.objectContaining({ title: 'Учебный приказ № 12' }),
        }),
      }),
    )
    expect(screen.getByLabelText('Сотрудник')).toHaveValue('employee-1')
    expect(screen.getByLabelText('Название и номер документа')).toHaveValue('Учебный приказ № 12')
    expect(screen.getByLabelText('Точная выдержка')).toHaveValue(
      'Назначить синтетического сотрудника с указанной даты.',
    )
    expect(screen.getByLabelText('Начало назначения')).toHaveValue('2026-10-28')
    expect(screen.getByLabelText(/Подтверждаю полномочия/)).toBeChecked()
    expect(saved).not.toHaveBeenCalled()
    expect(close).not.toHaveBeenCalled()
    await waitFor(() => expect(submit).toBeEnabled())
  })

  it('reuses the idempotency key for an unchanged retry and creates a new one after editing', async () => {
    const { saved, close, submit } = await fillAppointment()
    fireEvent.click(submit)
    await screen.findByRole('alert')
    await waitFor(() => expect(submit).toBeEnabled())
    const first = requests.post.mock.calls[0][1]
    expect(first.params.header['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/i)

    fireEvent.click(submit)
    await waitFor(() => expect(requests.post).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(submit).toBeEnabled())
    const retry = requests.post.mock.calls[1][1]
    expect(retry.body).toEqual(first.body)
    expect(retry.params.header['idempotency-key']).toBe(first.params.header['idempotency-key'])
    expect(saved).not.toHaveBeenCalled()
    expect(close).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Название и номер документа'), {
      target: { value: 'Учебный приказ № 13' },
    })
    requests.post.mockResolvedValueOnce({
      data: {
        event_id: 'event-1',
        appointment_id: 'appointment-1',
        replayed: false,
      } satisfies components['schemas']['EventResult'],
      response: new Response(null, { status: 200 }),
    })
    fireEvent.click(submit)
    await waitFor(() => expect(saved).toHaveBeenCalledTimes(1))
    expect(close).toHaveBeenCalledTimes(1)
    const edited = requests.post.mock.calls[2][1]
    expect(edited.body.basis.title).toBe('Учебный приказ № 13')
    expect(edited.params.header['idempotency-key']).not.toBe(first.params.header['idempotency-key'])
    expect(edited.params.header['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/i)
  })
})
