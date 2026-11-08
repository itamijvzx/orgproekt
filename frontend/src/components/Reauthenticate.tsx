import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { client, result, errorMessage } from '@/lib/client'
import { useLocalDemo } from '@/lib/authPolicy'

export function Reauthenticate({ onDone }: { onDone: () => void }) {
  const localDemo = useLocalDemo()
  const [password, setPassword] = useState(''),
    [code, setCode] = useState('')
  const [pending, setPending] = useState(false),
    [error, setError] = useState('')
  async function confirm() {
    setPending(true)
    setError('')
    try {
      await result(client.POST('/api/v1/auth/reauthenticate', { body: { password, code } }))
      setPassword('')
      setCode('')
      onDone()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPending(false)
    }
  }
  return (
    <div className="report-form">
      <h3>Подтвердите вход для этого действия</h3>
      <label>
        Текущий пароль
        <Input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {!localDemo && (
        <label>
          Новый код MFA или резервный код
          <Input
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
      )}
      {error && (
        <p role="alert" className="error-box">
          {error}
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={pending || !password || (!localDemo && !code)}
        onClick={() => void confirm()}
      >
        Подтвердить вход
      </Button>
    </div>
  )
}
