import { useEffect, useRef, useState, useCallback } from 'react'

const MAX_MESSAGES = 200
const MAX_RETRIES = 5
const BASE_DELAY = 1000

export function useSSE(url: string) {
  const [data, setData] = useState<string[]>([])
  const [connected, setConnected] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const retriesRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close()
    }

    const es = new EventSource(url)
    esRef.current = es

    es.onopen = () => {
      setConnected(true)
      retriesRef.current = 0
    }

    es.onmessage = (e) => {
      setData(prev => {
        const next = [...prev, e.data]
        return next.length > MAX_MESSAGES ? next.slice(-MAX_MESSAGES) : next
      })
    }

    es.onerror = () => {
      setConnected(false)
      es.close()

      if (retriesRef.current < MAX_RETRIES) {
        const delay = BASE_DELAY * Math.pow(2, retriesRef.current)
        retriesRef.current++
        timerRef.current = setTimeout(connect, delay)
      }
    }
  }, [url])

  useEffect(() => {
    connect()
    return () => {
      if (esRef.current) esRef.current.close()
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [connect])

  return { data, connected }
}
