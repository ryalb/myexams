// Tempo real (WebSocket) é opcional. Sem WebSocket (nuvem: Vercel), o app cai direto no
// polling de `useProcessingPoll`, sem abrir socket, sem erro no console e sem reconexão.

export interface RealtimeConfig {
  apiBaseUrl: string
  // Base do WebSocket (ex.: wss://api.exemplo.com.br). Vazio = derivada de `apiBaseUrl`.
  wsBaseUrl?: string
  // `false` (ou 'false') desliga o tempo real.
  realtime?: boolean | string
}

export function realtimeEnabled(config: RealtimeConfig): boolean {
  return !(config.realtime === false || config.realtime === 'false')
}

// Base `ws(s)://host` do socket, ou `null` quando o tempo real está desligado.
export function websocketBase(config: RealtimeConfig): string | null {
  if (!realtimeEnabled(config)) return null
  const explicit = (config.wsBaseUrl ?? '').trim().replace(/\/+$/, '')
  const base = explicit || config.apiBaseUrl.replace(/\/api\/v1\/?$/, '')
  return base.replace(/^http/, 'ws')
}
