import createClient from 'openapi-fetch'
import type { paths } from './schema.generated'

let csrf = ''
let lastUserActivityAt = Date.now()
const USER_ACTIVITY_WINDOW_MS = 60_000
for (const type of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'input']) {
  window.addEventListener(type, (event) => {
    if (event.isTrusted && !document.hidden) lastUserActivityAt = Date.now()
  })
}
document.addEventListener('visibilitychange', (event) => {
  if (event.isTrusted && !document.hidden) lastUserActivityAt = Date.now()
})
export const userRecentlyActive = () =>
  !document.hidden && Date.now() - lastUserActivityAt <= USER_ACTIVITY_WINDOW_MS

export const setCsrf = (value: string) => {
  csrf = value
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code = 'request_failed',
    public correlationId = '',
    public fields: { field: string; message: string }[] = [],
  ) {
    super(message)
  }
}

export const client = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: 'same-origin',
  cache: 'no-store',
  fetch: async (request) => {
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(15_000)])
    try {
      return await fetch(new Request(request, { signal }))
    } catch (error) {
      if (request.signal.aborted) throw error
      throw new ApiError(
        signal.aborted
          ? 'Сервер не ответил вовремя. Повторите запрос; неизменённая команда не создаст дубликат.'
          : 'Нет связи с сервером. Проверьте подключение и повторите запрос.',
        0,
        signal.aborted ? 'timeout' : 'network_error',
      )
    }
  },
})
client.use({
  onRequest({ request }) {
    if (!['GET', 'HEAD'].includes(request.method)) request.headers.set('X-CSRF-Token', csrf)
    // Background polling can display fresh data but cannot extend the server session.
    if (!userRecentlyActive())
      request.headers.set('X-OrgProekt-Background', '1')
    return request
  },
  onResponse({ response, request, schemaPath }) {
    if (response.ok && request.headers.get('X-OrgProekt-Background') !== '1')
      window.dispatchEvent(new CustomEvent('session-foreground', { detail: Date.now() }))
    if (response.status === 401 && !schemaPath.startsWith('/api/v1/auth/'))
      window.dispatchEvent(new Event('session-expired'))
    return response
  },
})

export async function result<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request
  if (!response.ok) {
    const body = (error && typeof error === 'object' ? error : {}) as Record<string, unknown>
    const message =
      typeof body.message === 'string'
        ? body.message
        : typeof body.detail === 'string'
          ? body.detail
          : 'Не удалось выполнить запрос'
    const fields = Array.isArray(body.field_errors)
      ? body.field_errors.filter(
          (f): f is { field: string; message: string } =>
            !!f && typeof f.field === 'string' && typeof f.message === 'string',
        )
      : []
    throw new ApiError(
      message,
      response.status,
      typeof body.code === 'string' ? body.code : 'request_failed',
      typeof body.correlation_id === 'string' ? body.correlation_id : '',
      fields,
    )
  }
  if (data === undefined)
    throw new ApiError('Сервер вернул неполный ответ', 502, 'invalid_response')
  return data
}

export const errorMessage = (error: unknown) => {
  if (error instanceof ApiError && error.status >= 500 && error.correlationId) {
    return `${error.message}. Код обращения: ${error.correlationId}`
  }
  return error instanceof Error ? error.message : 'Не удалось выполнить запрос'
}
