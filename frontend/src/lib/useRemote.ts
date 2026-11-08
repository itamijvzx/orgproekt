import { useEffect, useRef, useState } from 'react'
import { errorMessage } from './client'

export function useRemote<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const loader = useRef(load)
  loader.current = load
  const [state, setState] = useState<{
    key: string
    data: T | null
    error: string
    loading: boolean
  }>({ key: '', data: null, error: '', loading: true })
  useEffect(() => {
    const controller = new AbortController()
    setState({ key, data: null, error: '', loading: true })
    loader
      .current(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ key, data, error: '', loading: false })
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setState({ key, data: null, error: errorMessage(error), loading: false })
      })
    return () => controller.abort()
  }, [key])
  // Old records never appear beneath a new date/filter while its request is pending.
  return state.key === key
    ? { data: state.data, error: state.error, loading: state.loading }
    : { data: null, error: '', loading: true }
}
