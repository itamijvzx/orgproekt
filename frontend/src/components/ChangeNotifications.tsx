import { useEffect, useState } from 'react'
import { Bell, ArrowRight } from 'lucide-react'
import { client, errorMessage, result } from '@/lib/client'
import { useRemote } from '@/lib/useRemote'
import type { User } from '@/lib/types'
import { Button } from './ui/button'
import { LoadState, Pagination } from './ListControls'

const labels: Record<string, string> = {
  send: 'Предложение направлено на рассмотрение',
  message: 'Сообщение по предложению',
  review: 'Сохранён ответ согласующего',
  decision: 'Управленческое решение',
  execution: 'Сведения об исполнении',
  reminder: 'Напоминание о рассмотрении',
  escalation: 'Вопрос передан руководителю',
  version_created: 'Создана новая версия',
  created: 'Создано предложение',
  verified: 'Исполнение проверено',
  cancelled: 'Предложение отменено',
}

export function ChangeNotifications({
  revision,
  user,
  onOpen,
}: {
  revision: number
  user: User
  onOpen: (proposalId: string) => void
}) {
  const [offset, setOffset] = useState(0)
  const [tick, setTick] = useState(0)
  const [error, setError] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  useEffect(() => {
    const timer = setInterval(() => setTick((v) => v + 1), 15000)
    return () => clearInterval(timer)
  }, [])
  const state = useRemote(`change-notices:${offset}:${tick}:${revision}`, (signal) =>
    result(
      client.GET('/api/v1/changes/notifications', {
        params: { query: { offset, limit: 10 } },
        signal,
      }),
    ),
  )
  async function markRead(id: string) {
    setError('')
    setPending(id)
    try {
      await result(
        client.POST('/api/v1/changes/notifications/{notification_id}/read', {
          params: { path: { notification_id: id } },
        }),
      )
      setTick((v) => v + 1)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(null)
    }
  }
  return (
    <section className="content-panel notifications-panel" aria-label="Уведомления о предложениях">
      <div className="section-heading">
        <h2>Предложения и согласование</h2>
      </div>
      {error && (
        <p role="alert" className="error-box">
          {error}
        </p>
      )}
      <LoadState {...state} onRetry={() => setTick((v) => v + 1)} />
      {state.data && (
        <>
          {state.data.items.map((notice) => (
            <article
              key={notice.id}
              className={`notification-item ${notice.read_at ? '' : 'unread'}`}
            >
              <div className="notice-icon">
                <Bell size={18} />
              </div>
              <div>
                <h3>{notice.title}</h3>
                <p>{labels[notice.kind] ?? 'Обновление предложения'}</p>
                <small className="muted">
                  {new Date(notice.created_at).toLocaleString('ru-RU', { timeZone: user.timezone })}
                  {' · '}
                  {notice.read_at ? 'Прочитано' : 'Не прочитано'}
                </small>
                <div className="event-links">
                  <Button variant="outline" size="sm" onClick={() => onOpen(notice.proposal_id)}>
                    Открыть предложение <ArrowRight size={16} />
                  </Button>
                  {!notice.read_at && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={pending !== null}
                      onClick={() => void markRead(notice.id)}
                    >
                      {pending === notice.id ? 'Сохраняем…' : 'Отметить прочитанным'}
                    </Button>
                  )}
                </div>
              </div>
            </article>
          ))}
          {!state.data.total && (
            <div className="empty-state">
              <h3>Новых сообщений нет</h3>
              <p>Здесь появятся направленные вам предложения и ответы участников.</p>
            </div>
          )}
          <Pagination {...state.data} onChange={setOffset} />
        </>
      )}
    </section>
  )
}
