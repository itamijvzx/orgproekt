import { useState, type FormEvent } from 'react'
import { LogOut, ShieldCheck, UserRound } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MfaReplacement } from '@/components/MfaReplacement'
import { PasskeySettings } from '@/components/PasskeySettings'
import { OrganizationName } from '@/components/WorkspaceLists'
import { LoadState } from '@/components/ListControls'
import { client, result, errorMessage } from '@/lib/client'
import { useRemote } from '@/lib/useRemote'
import type { User } from '@/lib/types'
import { useLocalDemo } from '@/lib/authPolicy'

export function AccountSettings({
  user,
  onLogout,
  onSaved,
}: {
  user: User
  onLogout: () => void
  onSaved: () => void
}) {
  const localDemo = useLocalDemo()
  const [revision, setRevision] = useState(0)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState<'reauth' | 'revoke' | 'retry' | null>(null)
  const [error, setError] = useState('')
  const sessions = useRemote(`sessions:${revision}`, (signal) =>
    result(client.GET('/api/v1/auth/sessions', { signal })),
  )
  const system = useRemote(`system:${revision}`, (signal) =>
    result(client.GET('/api/v1/system', { signal })),
  )
  const refresh = () => setRevision((value) => value + 1)
  const canRetry = user.roles.some((grant) => grant.role === 'hr' || grant.role === 'auditor')

  async function reauthenticate(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy('reauth')
    setError('')
    try {
      await result(client.POST('/api/v1/auth/reauthenticate', { body: { password, code } }))
      setPassword('')
      setCode('')
      toast.success('Вход подтверждён на следующие 10 минут')
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      setBusy(null)
    }
  }

  async function revokeOthers() {
    if (busy) return
    setBusy('revoke')
    setError('')
    try {
      await result(client.POST('/api/v1/auth/sessions/revoke-others'))
      refresh()
      toast.success('Другие сессии завершены')
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      setBusy(null)
    }
  }

  async function retryDelivery() {
    if (busy) return
    setBusy('retry')
    setError('')
    try {
      const response = await result(client.POST('/api/v1/outbox/retry-failed'))
      toast.success(`Повторно поставлено в очередь: ${response.retried}`)
      refresh()
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      {error && (
        <div className="error-box" role="alert">
          {error}
        </div>
      )}
      <div className="settings-layout">
        <section className="content-panel">
          <div className="section-heading">
            <ShieldCheck size={20} />
            <h2>Безопасность входа</h2>
          </div>
          <p className="muted">
            {user.display_name} · {user.username}. Для регистрации кадровых событий и отзыва других
            сессий нужен вход, подтверждённый не более 10 минут назад.
          </p>
          <form className="reauth-form" onSubmit={reauthenticate} aria-busy={busy === 'reauth'}>
            <div className="field">
              <Label htmlFor="reauth-password">Текущий пароль</Label>
              <Input
                id="reauth-password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={200}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {!localDemo && (
              <div className="field">
                <Label htmlFor="reauth-code">Новый код MFA или резервный код</Label>
                <Input
                  id="reauth-code"
                  autoComplete="one-time-code"
                  required
                  maxLength={64}
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                />
              </div>
            )}
            <Button disabled={!!busy}>
              {busy === 'reauth' ? 'Проверяем…' : 'Подтвердить вход'}
            </Button>
          </form>
          {localDemo ? (
            <p className="muted">
              Локальный учебный режим: MFA отключена. Важные действия подтверждаются текущим
              паролем.
            </p>
          ) : (
            <>
              <PasskeySettings />
              <MfaReplacement onSaved={refresh} />
            </>
          )}
          <h3>Активные сессии</h3>
          <LoadState {...sessions} onRetry={refresh} />
          {sessions.data?.map((session) => (
            <div className="session-row" key={session.id}>
              <UserRound size={18} />
              <div>
                <strong>{session.current ? 'Текущая сессия' : 'Другая сессия'}</strong>
                <span>
                  До{' '}
                  {new Date(session.expires_at).toLocaleString('ru-RU', {
                    timeZone: user.timezone,
                  })}
                </span>
              </div>
            </div>
          ))}
          {sessions.data?.length === 0 && <p className="muted">Активных сессий не найдено.</p>}
          <div className="settings-actions">
            <Button variant="outline" disabled={!!busy} onClick={revokeOthers}>
              {busy === 'revoke' ? 'Завершаем…' : 'Завершить другие сессии'}
            </Button>
            <Button variant="ghost" disabled={!!busy} onClick={onLogout}>
              <LogOut size={16} />
              Выйти
            </Button>
          </div>
        </section>
        <section className="content-panel system-panel">
          <div className="eyebrow">СОСТОЯНИЕ СИСТЕМЫ</div>
          <h2>Работает без Gemini</h2>
          <p>
            Структура, кадровые события, права и история используют серверный реестр и
            детерминированные расчёты.
          </p>
          <LoadState {...system} onRetry={refresh} />
          {system.data && (
            <dl>
              <div>
                <dt>Режим</dt>
                <dd>{system.data.mode === 'synthetic' ? 'Учебные данные' : system.data.mode}</dd>
              </div>
              <div>
                <dt>ИИ-анализ</dt>
                <dd>
                  {system.data.ai_status === 'not_configured'
                    ? 'Адаптер готов · ключ или модель не настроены'
                    : system.data.ai_status === 'disabled'
                      ? 'Адаптер готов · внешние вызовы выключены'
                      : 'Адаптер настроен · рабочий ИИ-процесс ещё не подключён'}
                </dd>
              </div>
              <div>
                <dt>Ключ Gemini</dt>
                <dd>{system.data.key_configured ? 'Добавлен на сервере' : 'Не добавлен'}</dd>
              </div>
              <div>
                <dt>Уведомления в очереди</dt>
                <dd>{system.data.pending_notifications}</dd>
              </div>
              <div>
                <dt>Ошибки доставки</dt>
                <dd>{system.data.failed_notifications}</dd>
              </div>
            </dl>
          )}
          {!!system.data?.failed_notifications && canRetry && (
            <Button variant="outline" disabled={!!busy} onClick={retryDelivery}>
              {busy === 'retry' ? 'Отправляем…' : 'Повторить доставку'}
            </Button>
          )}
          <p className="small muted">
            Ключ добавляется владельцем только в локальный .env. Ввод в браузере не требуется.
            Оригиналы DOCX/PDF доступны в разделе «Документы». Адаптер Gemini подготовлен; сравнение
            и управляемый ИИ-процесс ещё не подключены к рабочим сценариям.
          </p>
        </section>
      </div>
      <OrganizationName onSaved={onSaved} />
    </>
  )
}
