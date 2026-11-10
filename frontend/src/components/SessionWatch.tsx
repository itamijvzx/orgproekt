import { useCallback, useEffect, useRef, useState } from 'react'
import { Clock3 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ApiError, client, errorMessage, result, userRecentlyActive } from '@/lib/client'
import type { User } from '@/lib/types'

const WARNING_MS = 5 * 60_000
const HEARTBEAT_MS = 60_000

export function SessionWatch({
  user,
  onRenewed,
}: {
  user: User
  onRenewed: (next: User) => void
}) {
  const [currentTime, setCurrentTime] = useState(Date.now())
  const [lastForegroundAt, setLastForegroundAt] = useState(
    Date.parse(user.idle_expires_at) - user.session_idle_seconds * 1000,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef(false)
  const lastHeartbeatAt = useRef(Date.now())
  const lastExpiryCheckAt = useRef(0)

  const renew = useCallback(async () => {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    try {
      onRenewed(await result(client.GET('/api/v1/auth/me')))
      setError('')
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401)
        window.dispatchEvent(new Event('session-expired'))
      else setError(errorMessage(cause))
    } finally {
      pending.current = false
      setBusy(false)
    }
  }, [onRenewed])

  useEffect(() => {
    const foreground = (event: Event) => {
      const instant = (event as CustomEvent<number>).detail
      if (typeof instant === 'number') setLastForegroundAt(instant)
    }
    window.addEventListener('session-foreground', foreground)
    return () => window.removeEventListener('session-foreground', foreground)
  }, [])

  const idleDeadline = Math.max(
    Date.parse(user.idle_expires_at),
    lastForegroundAt + user.session_idle_seconds * 1000,
  )
  const absoluteDeadline = Date.parse(user.session_expires_at)
  const deadline = Math.min(idleDeadline, absoluteDeadline)
  const absoluteLimit = absoluteDeadline <= idleDeadline
  const remaining = deadline - currentTime

  useEffect(() => {
    const timer = window.setInterval(() => {
      const instant = Date.now()
      setCurrentTime(instant)
      if (instant >= deadline) {
        if (instant - lastExpiryCheckAt.current >= HEARTBEAT_MS) {
          lastExpiryCheckAt.current = instant
          void renew()
        }
      } else if (userRecentlyActive() && instant - lastHeartbeatAt.current >= HEARTBEAT_MS) {
        lastHeartbeatAt.current = instant
        void renew()
      }
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [deadline, renew])

  if (remaining > WARNING_MS) return null
  const minutes = Math.max(1, Math.ceil(remaining / 60_000))
  return (
    <aside className="session-warning" role="status" aria-live="polite">
      <Clock3 size={20} aria-hidden="true" />
      <div className="session-warning-copy">
        <strong>{remaining > 0 ? `Сеанс завершится примерно через ${minutes} мин.` : 'Проверяем сеанс…'}</strong>
        <span>
          {absoluteLimit
            ? 'Общий срок входа заканчивается. Сохраните открытый черновик и войдите заново.'
            : 'Сохраните открытый черновик или продолжите работу, чтобы не потерять текст.'}
        </span>
        {error && <small>{error}</small>}
      </div>
      {!absoluteLimit && remaining > 0 && (
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void renew()}>
          {busy ? 'Проверяем…' : 'Продолжить сеанс'}
        </Button>
      )}
    </aside>
  )
}
