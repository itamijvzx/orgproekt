import { useState, type FormEvent } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { client, result, errorMessage, setCsrf } from '@/lib/client'
import type { components } from '@/lib/schema.generated'

export function MfaReplacement({ onSaved }: { onSaved?: () => void }) {
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [enrollment, setEnrollment] = useState<components['schemas']['Enrollment'] | null>(null)
  const [codes, setCodes] = useState<string[]>([])
  const [saved, setSaved] = useState(false)

  function close() {
    setOpen(false)
    setPassword('')
    setCode('')
    setError('')
    setEnrollment(null)
    setCodes([])
    setSaved(false)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      if (!enrollment) {
        setEnrollment(
          await result(client.POST('/api/v1/auth/mfa/replace', { body: { password, code } })),
        )
        setPassword('')
        setCode('')
      } else {
        const response = await result(
          client.POST('/api/v1/auth/mfa/replace/confirm', { body: { code } }),
        )
        setCsrf(response.csrf_token)
        setCodes(response.recovery_codes)
        setEnrollment(null)
        setCode('')
        setSaved(false)
        onSaved?.()
      }
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        Заменить аутентификатор
      </Button>
      <Dialog
        open={open}
        onOpenChange={(value) => {
          if (!value && !busy && (!codes.length || saved)) close()
        }}
      >
        <DialogContent className="personnel-dialog">
          <DialogHeader>
            <DialogTitle>
              {codes.length ? 'Новые резервные коды' : 'Замена второго фактора'}
            </DialogTitle>
            <DialogDescription>
              Старый фактор действует до подтверждения нового. Затем другие сессии будут завершены,
              прежние резервные коды отозваны.
            </DialogDescription>
          </DialogHeader>
          {error && (
            <div className="error-box" role="alert">
              {error}
            </div>
          )}
          {codes.length ? (
            <>
              <div className="recovery-grid">
                {codes.map((code) => (
                  <code key={code}>{code}</code>
                ))}
              </div>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={saved}
                  onChange={(event) => setSaved(event.target.checked)}
                />
                Новые коды сохранены в защищённом месте
              </label>
              <Button disabled={!saved} onClick={close}>
                Завершить
              </Button>
            </>
          ) : (
            <form onSubmit={submit} className="event-form" aria-busy={busy}>
              {!enrollment ? (
                <>
                  <div className="field">
                    <Label htmlFor="mfa-password">Пароль</Label>
                    <Input
                      id="mfa-password"
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                      maxLength={200}
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="old-mfa">
                      Код текущего аутентификатора или неиспользованный резервный код
                    </Label>
                    <Input
                      id="old-mfa"
                      autoComplete="one-time-code"
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      required
                      maxLength={64}
                    />
                  </div>
                </>
              ) : (
                <>
                  <div className="mfa-enrollment">
                    <QRCodeSVG
                      value={enrollment.uri}
                      size={152}
                      aria-label="QR-код нового аутентификатора"
                    />
                    <details>
                      <summary>Ввести ключ вручную</summary>
                      <code>{enrollment.secret}</code>
                    </details>
                  </div>
                  <div className="field">
                    <Label htmlFor="new-mfa">Код нового аутентификатора</Label>
                    <Input
                      id="new-mfa"
                      autoComplete="one-time-code"
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      required
                      maxLength={64}
                    />
                  </div>
                </>
              )}
              <Button disabled={busy}>
                {busy ? 'Проверяем…' : enrollment ? 'Подтвердить новый фактор' : 'Начать замену'}
              </Button>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
