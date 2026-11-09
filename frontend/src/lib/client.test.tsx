import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('marks automatic requests as background after browser inactivity', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-09T10:00:00Z'))
  const requests: Request[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      requests.push(request)
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
    }),
  )
  const { client } = await import('./client')
  await client.GET('/api/v1/auth/policy')
  expect(requests[0].headers.get('X-OrgProekt-Background')).toBeNull()

  vi.setSystemTime(new Date('2026-11-09T10:01:01Z'))
  await client.GET('/api/v1/auth/policy')
  expect(requests[1].headers.get('X-OrgProekt-Background')).toBe('1')
})
