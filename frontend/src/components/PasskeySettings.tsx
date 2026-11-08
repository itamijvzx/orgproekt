import { useState } from 'react'
import { Fingerprint } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { client, result, ApiError } from '@/lib/client'
import {
  passkeyMessage,
  registerPasskey,
  supportsPasskeys,
  usePasskeysEnabled,
} from '@/lib/passkeys'
import { useRemote } from '@/lib/useRemote'

export function PasskeySettings() {
  const enabled = usePasskeysEnabled()
  return enabled ? <KeyList /> : null
}

function KeyList() {
  const [revision, setRevision] = useState(0)
  const [name, setName] = useState('Этот компьютер')
  const [pending, setPending] = useState(false)
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const keys = useRemote('passkeys:' + revision, (signal) =>
    result(client.GET('/api/v1/auth/passkeys', { signal })),
  )
  async function change(id?: string) {
    if (pending) return
    setPending(true)
    setError('')
    setNotice('')
    try {
      if (id) {
        await result(
          client.DELETE('/api/v1/auth/passkeys/{key_id}', { params: { path: { key_id: id } } }),
        )
        setNotice('Ключ отозван. Другие сессии завершены.')
      } else {
        await registerPasskey(name.trim())
        setNotice('Ключ подключён. При следующем входе нажмите «Войти по ключу доступа».')
      }
      setConfirm('')
      setRevision((r) => r + 1)
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? 'Подтвердите вход паролем и свежим кодом в форме выше, затем повторите действие.'
          : passkeyMessage(e),
      )
    } finally {
      setPending(false)
    }
  }
  return (
    <section aria-labelledby="passkeys-heading" className="passkey-settings">
      <h3 id="passkeys-heading">Ключи доступа</h3>
      <p className="muted">
        Вход с этого устройства по PIN или биометрии Windows Hello. PIN вводится в системном окне
        Windows.
      </p>
      {keys.loading && <p role="status">Загружаем список ключей…</p>}
      {(keys.error || error) && (
        <p role="alert" className="error-box">
          {error || keys.error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {keys.data?.map((key) => (
        <div className="session-row" key={key.id}>
          <Fingerprint size={18} />
          <div>
            <strong>{key.name}</strong>
            <span>Добавлен {new Date(key.installed_at).toLocaleDateString('ru-RU')}</span>
          </div>
          {confirm === key.id ? (
            <>
              <Button variant="outline" disabled={pending} onClick={() => void change(key.id)}>
                Подтвердить отзыв
              </Button>
              <Button variant="ghost" disabled={pending} onClick={() => setConfirm('')}>
                Отмена
              </Button>
            </>
          ) : (
            <Button variant="ghost" disabled={pending} onClick={() => setConfirm(key.id)}>
              Отозвать
            </Button>
          )}
        </div>
      ))}
      {keys.data?.length === 0 && <p className="small muted">Подключённых ключей пока нет.</p>}
      {supportsPasskeys() ? (
        <form
          className="auth-form"
          onSubmit={(e) => {
            e.preventDefault()
            void change()
          }}
        >
          <div className="field">
            <Label htmlFor="passkey-name">Название ключа</Label>
            <Input
              id="passkey-name"
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <Button
            type="submit"
            variant="outline"
            disabled={pending || !name.trim() || (keys.data?.length ?? 0) >= 5}
          >
            <Fingerprint size={16} />
            {pending ? 'Подождите…' : 'Добавить ключ Windows Hello'}
          </Button>
        </form>
      ) : (
        <p>
          Для подключения ключа откройте учебный HTTPS-сайт в актуальном Microsoft Edge или Chrome.
        </p>
      )}
    </section>
  )
}
