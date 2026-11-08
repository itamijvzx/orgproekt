import { useState, type FormEvent } from 'react'
import { ArrowRight, Fingerprint, KeyRound } from 'lucide-react'
import {
  loginWithPasskey,
  passkeyMessage,
  supportsPasskeys,
  usePasskeysEnabled,
} from '@/lib/passkeys'
import { QRCodeSVG } from 'qrcode.react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ShimmerButton } from '@/components/ui/shimmer-button'
import { client, result, setCsrf, errorMessage } from '@/lib/client'
import type { components } from '@/lib/schema.generated'
import { PRODUCT_NAME } from '@/lib/branding'
import { useLocalDemo } from '@/lib/authPolicy'

export function AuthScreen({ onReady }: { onReady: () => void }) {
  const [step, setStep] = useState<'login' | 'mfa' | 'enroll' | 'codes' | 'reset' | 'invite'>(
    'login',
  )
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [token, setToken] = useState('')
  const [enrollment, setEnrollment] = useState<components['schemas']['Enrollment'] | null>(null)
  const [codes, setCodes] = useState<string[]>([])
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const passkeysEnabled = usePasskeysEnabled()
  const localDemo = useLocalDemo()

  async function useKey() {
    if (busy) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await loginWithPasskey()
      onReady()
    } catch (e) {
      setError(passkeyMessage(e))
    } finally {
      setBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      if (step === 'login') {
        const response = await result(
          client.POST('/api/v1/auth/login', { body: { username, password } }),
        )
        setCsrf(response.csrf_token)
        setPassword('')
        if (response.authenticated) {
          onReady()
        } else if (response.enrollment_required) {
          setEnrollment(await result(client.POST('/api/v1/auth/mfa/enroll')))
          setStep('enroll')
        } else setStep('mfa')
      } else if (step === 'enroll') {
        const response = await result(client.POST('/api/v1/auth/mfa/confirm', { body: { code } }))
        setCsrf(response.csrf_token)
        setCode('')
        setEnrollment(null)
        setCodes(response.recovery_codes)
        setSaved(false)
        setStep('codes')
      } else if (step === 'mfa') {
        const response = await result(client.POST('/api/v1/auth/mfa/verify', { body: { code } }))
        setCsrf(response.csrf_token)
        setCode('')
        onReady()
      } else if (step === 'reset') {
        await result(client.POST('/api/v1/auth/reset-password', { body: { token, password } }))
        setToken('')
        setPassword('')
        setStep('login')
        setMessage(
          localDemo
            ? 'Пароль изменён. Войдите с новым паролем.'
            : 'Пароль изменён. Войдите и подтвердите MFA.',
        )
      } else if (step === 'invite') {
        await result(client.POST('/api/v1/auth/accept-invitation', { body: { token, password } }))
        setToken('')
        setPassword('')
        setStep('login')
        setMessage(
          localDemo
            ? 'Учётная запись создана. Войдите с назначенным логином и своим паролем. Права на отдел назначаются отдельно.'
            : 'Учётная запись создана. Войдите с назначенным логином и своим паролем, затем подключите MFA. Права на отдел назначаются отдельно.',
        )
      }
    } catch (error) {
      setError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const title = {
    login: `Войти в ${PRODUCT_NAME}`,
    enroll: 'Защитите свой вход',
    codes: 'Сохраните резервные коды',
    reset: 'Восстановление пароля',
    mfa: 'Подтвердите вход',
    invite: 'Активировать приглашение',
  }[step]
  const description = {
    login: 'Введите данные своей личной учётной записи.',
    enroll: 'Добавьте OrgProekt в приложение-аутентификатор и введите одноразовый код.',
    codes: 'Каждый код работает один раз. Храните их в защищённом месте отдельно от пароля.',
    reset: 'Используйте одноразовый токен, полученный от владельца стенда после проверки личности.',
    mfa: 'Введите код аутентификатора или один из сохранённых резервных кодов.',
    invite:
      'Введите код от администратора и задайте личный пароль. Код приглашения действует ограниченное время и используется один раз.',
  }[step]

  return (
    <main className="auth-shell">
      <div className="auth-main">
        <div className="auth-form-wrap">
          <div className="auth-brand brand">
            <span>{PRODUCT_NAME}</span>
          </div>
          <h1>{title}</h1>
          <p className="muted">{description}</p>
          {error && (
            <div className="error-box" role="alert">
              {error}
            </div>
          )}
          {message && (
            <div className="success-box" role="status">
              {message}
            </div>
          )}

          {step === 'codes' ? (
            <div>
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
                Я сохранил(а) резервные коды
              </label>
              <Button
                className="w-full"
                disabled={!saved}
                onClick={() => {
                  setCodes([])
                  onReady()
                }}
              >
                Открыть рабочее пространство <ArrowRight />
              </Button>
            </div>
          ) : (
            <form onSubmit={submit} className="auth-form" aria-busy={busy}>
              {step === 'login' && (
                <>
                  <div className="field">
                    <Label htmlFor="username">Логин</Label>
                    <Input
                      id="username"
                      name="username"
                      autoComplete="username"
                      value={username}
                      onChange={(event) => setUsername(event.target.value)}
                      placeholder="Ваш личный логин"
                      required
                      autoFocus
                      maxLength={160}
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="password">Пароль</Label>
                    <Input
                      id="password"
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                      maxLength={200}
                    />
                  </div>
                </>
              )}
              {step === 'enroll' && enrollment && (
                <div className="mfa-enrollment">
                  <QRCodeSVG
                    value={enrollment.uri}
                    size={152}
                    aria-label="QR-код подключения аутентификатора"
                  />
                  <details>
                    <summary>Ввести ключ вручную</summary>
                    <code data-testid="mfa-secret">{enrollment.secret}</code>
                  </details>
                </div>
              )}
              {(step === 'enroll' || step === 'mfa') && (
                <div className="field">
                  <Label htmlFor="mfa-code">
                    Код аутентификатора{step === 'mfa' ? ' или резервный код' : ''}
                  </Label>
                  <Input
                    id="mfa-code"
                    autoComplete="one-time-code"
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    placeholder="000000"
                    required
                    autoFocus
                    maxLength={64}
                  />
                </div>
              )}
              {(step === 'reset' || step === 'invite') && (
                <>
                  <div className="field">
                    <Label htmlFor="token">
                      {step === 'invite' ? 'Код приглашения' : 'Одноразовый токен восстановления'}
                    </Label>
                    <Input
                      id="token"
                      autoComplete="off"
                      value={token}
                      onChange={(event) => setToken(event.target.value)}
                      required
                    />
                  </div>
                  <div className="field">
                    <Label htmlFor="new-password">Новый пароль · от 14 символов</Label>
                    <Input
                      id="new-password"
                      type="password"
                      autoComplete="new-password"
                      minLength={14}
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                    />
                  </div>
                  <p className="small muted">
                    {step === 'invite'
                      ? 'После активации войдите в аккаунт и настройте второй фактор.'
                      : 'Сброс пароля сохраняет MFA и действующие права.'}
                  </p>
                </>
              )}
              <ShimmerButton
                type="submit"
                disabled={busy}
                background="var(--primary)"
                borderRadius="7px"
                shimmerColor="var(--primary-foreground)"
                shimmerDuration="5s"
                className="auth-submit"
              >
                {busy
                  ? 'Проверяем…'
                  : step === 'login'
                    ? 'Продолжить'
                    : step === 'reset'
                      ? 'Изменить пароль'
                      : step === 'invite'
                        ? 'Создать личную учётную запись'
                        : 'Подтвердить код'}
                <ArrowRight size={17} />
              </ShimmerButton>
            </form>
          )}
          {step === 'login' && passkeysEnabled && supportsPasskeys() && (
            <Button
              variant="outline"
              className="w-full passkey-login"
              disabled={busy}
              onClick={() => void useKey()}
            >
              <Fingerprint size={17} /> Войти по ключу доступа
            </Button>
          )}
          {step === 'login' && (
            <button
              className="text-button recovery-link"
              disabled={busy}
              onClick={() => {
                setError('')
                setMessage('')
                setToken('')
                setPassword('')
                setStep('invite')
              }}
            >
              У меня есть приглашение
            </button>
          )}
          {step === 'login' && (
            <button
              className="text-button recovery-link"
              disabled={busy}
              onClick={() => {
                setError('')
                setStep('reset')
              }}
            >
              Не удаётся войти?
            </button>
          )}
          {step !== 'login' && step !== 'codes' && (
            <button
              className="text-button recovery-link"
              disabled={busy}
              onClick={() => {
                setError('')
                setEnrollment(null)
                setCode('')
                setToken('')
                setPassword('')
                setStep('login')
              }}
            >
              Вернуться ко входу
            </button>
          )}
          <div className="auth-note">
            <KeyRound size={15} />
            <span>
              {localDemo
                ? 'Локальный учебный режим · MFA отключена'
                : 'Личный вход · двухфакторная защита'}
            </span>
          </div>
        </div>
      </div>
    </main>
  )
}
