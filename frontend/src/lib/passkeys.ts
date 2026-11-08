import { client, result, setCsrf } from './client'
import { useRemote } from './useRemote'

export function usePasskeysEnabled() {
  return (
    useRemote('passkeys-enabled', (signal) =>
      window.location.protocol === 'https:'
        ? result(client.GET('/api/v1/auth/passkeys/availability', { signal }))
        : Promise.resolve({ enabled: false }),
    ).data?.enabled === true
  )
}

export function supportsPasskeys() {
  return (
    window.isSecureContext &&
    typeof PublicKeyCredential !== 'undefined' &&
    typeof PublicKeyCredential.parseCreationOptionsFromJSON === 'function' &&
    typeof PublicKeyCredential.parseRequestOptionsFromJSON === 'function' &&
    typeof PublicKeyCredential.prototype.toJSON === 'function'
  )
}

export function passkeyMessage(error: unknown) {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError' || error.name === 'AbortError')
      return 'Подтверждение отменено, истекло время или на устройстве нет подходящего ключа.'
    if (error.name === 'InvalidStateError') return 'На этом устройстве уже есть ключ для аккаунта.'
    return 'Браузер не смог использовать ключ. Проверьте Windows Hello и адрес учебной копии.'
  }
  return error instanceof Error ? error.message : 'Не удалось использовать ключ доступа.'
}

export async function registerPasskey(name: string) {
  const options = await result(
    client.POST('/api/v1/auth/passkeys/register/options', { body: { name } }),
  )
  const publicKey = PublicKeyCredential.parseCreationOptionsFromJSON(options.publicKey)
  const key = await navigator.credentials.create({ publicKey })
  if (!(key instanceof PublicKeyCredential)) throw new Error('Ключ не создан.')
  const { id, rawId, response } = key.toJSON() as RegistrationResponseJSON
  return result(
    client.POST('/api/v1/auth/passkeys/register/verify', {
      body: {
        id,
        rawId,
        type: 'public-key',
        response: {
          clientDataJSON: response.clientDataJSON,
          attestationObject: response.attestationObject,
        },
      },
    }),
  )
}

export async function loginWithPasskey() {
  const options = await result(client.POST('/api/v1/auth/passkeys/login/options'))
  const publicKey = PublicKeyCredential.parseRequestOptionsFromJSON(options.publicKey)
  const key = await navigator.credentials.get({ publicKey })
  if (!(key instanceof PublicKeyCredential)) throw new Error('Ключ не выбран.')
  const { id, rawId, response } = key.toJSON() as AuthenticationResponseJSON
  if (!response.userHandle) throw new Error('Этот ключ не связан с учётной записью.')
  const session = await result(
    client.POST('/api/v1/auth/passkeys/login/verify', {
      body: {
        id,
        rawId,
        type: 'public-key',
        response: {
          clientDataJSON: response.clientDataJSON,
          authenticatorData: response.authenticatorData,
          signature: response.signature,
          userHandle: response.userHandle,
        },
      },
    }),
  )
  setCsrf(session.csrf_token)
}
