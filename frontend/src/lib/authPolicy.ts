import { client, result } from './client'
import { useRemote } from './useRemote'

export function useLocalDemo() {
  return (
    useRemote('auth-policy', (signal) => result(client.GET('/api/v1/auth/policy', { signal }))).data
      ?.local_demo === true
  )
}
