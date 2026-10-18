interface DocumentEvent {
  status: string
  documentId: number
}

// Estado compartilhado: qualquer componente reage ao evento sem abrir uma segunda
// conexão WebSocket.
export function useDocumentEvent() {
  return useState<DocumentEvent | null>('lastDocumentEvent', () => null)
}

const TERMINAL = new Set(['completed', 'needs_review', 'failed'])

// O backend aceita a conexão e só depois fecha com 4001 se o token for recusado:
// `onopen` sozinho não prova que a sessão está boa. A conexão só conta como saudável
// depois de uma mensagem ou de ficar aberta por este tempo.
const STABLE_AFTER_MS = 5000
const BACKOFF_START_MS = 3000
const BACKOFF_MAX_MS = 30000
const MAX_AUTH_FAILURES = 3

export function useDocumentEvents() {
  const config = useRuntimeConfig()
  const nuxtApp = useNuxtApp()
  const router = useRouter()
  const { accessToken, refresh, logout } = useAuth()
  const lastEvent = useDocumentEvent()
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let stableTimer: ReturnType<typeof setTimeout> | undefined
  let consecutiveAuthFailures = 0
  let backoff = BACKOFF_START_MS
  // Houve queda desde a última conexão saudável: eventos podem ter sido perdidos.
  let needsResync = false
  let active = false

  // Callbacks assíncronos (timers, socket) rodam fora do contexto do Nuxt.
  const resync = () => nuxtApp.runWithContext(() => refreshNuxtData())

  const clearTimers = () => {
    clearTimeout(reconnectTimer)
    clearTimeout(stableTimer)
    reconnectTimer = undefined
    stableTimer = undefined
  }

  // `onclose = null` antes de `close()`: sem isso o fechamento voluntário
  // (logout, troca de layout) agendaria uma reconexão "zumbi".
  const detach = () => {
    if (!ws) return
    ws.onopen = null
    ws.onmessage = null
    ws.onclose = null
    ws.close()
    ws = null
  }

  const markHealthy = () => {
    consecutiveAuthFailures = 0
    backoff = BACKOFF_START_MS
    if (needsResync) {
      needsResync = false
      resync()
    }
  }

  const scheduleReconnect = () => {
    clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(connect, backoff)
    backoff = Math.min(backoff * 2, BACKOFF_MAX_MS)
  }

  const goToLogin = () => {
    logout()
    router.push('/login')
  }

  function connect() {
    if (!accessToken.value || typeof WebSocket === 'undefined') return
    clearTimers()
    detach()
    active = true
    const wsBase = config.public.apiBaseUrl.replace(/^http/, 'ws').replace(/\/api\/v1\/?$/, '')
    const socket = new WebSocket(`${wsBase}/ws/documents/?token=${encodeURIComponent(accessToken.value)}`)
    ws = socket

    socket.onopen = () => {
      stableTimer = setTimeout(markHealthy, STABLE_AFTER_MS)
    }

    socket.onmessage = (event) => {
      markHealthy()
      try {
        const data = JSON.parse(event.data)
        if (data.type !== 'document_event') return
        lastEvent.value = { status: data.status, documentId: data.document_id }
        if (TERMINAL.has(data.status)) resync()
      } catch {
        // mensagem malformada: ignora, o próximo evento corrige o estado
      }
    }

    // 4001 = o backend recusou o token. Reconectar com o mesmo token repetiria a
    // falha para sempre: renova primeiro e, se continuar falhando, desloga.
    socket.onclose = async (event) => {
      if (ws !== socket) return
      ws = null
      clearTimeout(stableTimer)
      needsResync = true
      if (event.code === 4001) {
        consecutiveAuthFailures += 1
        if (consecutiveAuthFailures >= MAX_AUTH_FAILURES) {
          goToLogin()
          return
        }
        const renewed = await refresh()
        if (!active) return
        if (renewed) connect()
        else goToLogin()
        return
      }
      scheduleReconnect()
    }
  }

  const disconnect = () => {
    active = false
    needsResync = false
    clearTimers()
    detach()
  }

  return { connect, disconnect }
}
