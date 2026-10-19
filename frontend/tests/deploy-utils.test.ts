// `bun test` (sem dependências novas). Funções puras de `utils/`; dados inventados.
import { describe, expect, test } from 'bun:test'
import { realtimeEnabled, websocketBase } from '../utils/realtime'
import { exceedsUploadLimit, uploadLimitBytes } from '../utils/upload'

const API = 'http://localhost:8100/api/v1'

describe('websocketBase', () => {
  test('sem configuração nova, deriva do apiBaseUrl como sempre', () => {
    expect(websocketBase({ apiBaseUrl: API })).toBe('ws://localhost:8100')
    expect(websocketBase({ apiBaseUrl: 'https://api.exemplo.test/api/v1/' })).toBe('wss://api.exemplo.test')
  })

  test('wsBaseUrl explícito vale, e http vira ws', () => {
    expect(websocketBase({ apiBaseUrl: API, wsBaseUrl: 'wss://ws.exemplo.test/' })).toBe('wss://ws.exemplo.test')
    expect(websocketBase({ apiBaseUrl: API, wsBaseUrl: 'https://ws.exemplo.test' })).toBe('wss://ws.exemplo.test')
  })

  test('tempo real desligado não devolve base nenhuma', () => {
    expect(websocketBase({ apiBaseUrl: API, realtime: false })).toBeNull()
    expect(websocketBase({ apiBaseUrl: API, realtime: 'false', wsBaseUrl: 'wss://x.test' })).toBeNull()
  })
})

describe('realtimeEnabled', () => {
  test('ligado por padrão', () => {
    expect(realtimeEnabled({ apiBaseUrl: API })).toBe(true)
    expect(realtimeEnabled({ apiBaseUrl: API, realtime: true })).toBe(true)
    expect(realtimeEnabled({ apiBaseUrl: API, realtime: 'true' })).toBe(true)
  })
})

describe('limite de upload', () => {
  test('sem limite conhecido, nunca bloqueia no cliente', () => {
    expect(uploadLimitBytes(null)).toBeNull()
    expect(uploadLimitBytes(0)).toBeNull()
    expect(exceedsUploadLimit(10 ** 9, undefined)).toBe(false)
  })

  test('compara em MiB como o backend', () => {
    expect(uploadLimitBytes(4)).toBe(4 * 1024 * 1024)
    expect(exceedsUploadLimit(4 * 1024 * 1024, 4)).toBe(false)
    expect(exceedsUploadLimit(4 * 1024 * 1024 + 1, 4)).toBe(true)
  })
})
