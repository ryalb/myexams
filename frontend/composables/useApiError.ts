// O backend não produz texto de interface: devolve `{code, params}` (ou, em erros
// de campo do DRF, uma lista cujo item é um código). Aqui o código vira a frase
// em pt-BR de `errors.<code>`. Mensagens nativas do DRF já chegam em pt-BR.
type ApiErrorBody = {
  code?: string
  params?: Record<string, unknown>
  detail?: string
} & Record<string, unknown>

export function useApiError() {
  const { t, te, d } = useI18n()

  const localParams = (params: Record<string, unknown> = {}) => {
    const formatted: Record<string, unknown> = { ...params }
    for (const [key, value] of Object.entries(params)) {
      if (/date/i.test(key) && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) {
        const [year, month, day] = value.slice(0, 10).split('-').map(Number)
        // Meio-dia UTC: o formato 'short' fixa America/Sao_Paulo (ver useFormat).
        formatted[key] = d(new Date(Date.UTC(year, month - 1, day, 12)), 'short')
      }
    }
    return formatted
  }

  const fromCode = (code: string, params?: Record<string, unknown>): string | null =>
    te(`errors.${code}`) ? t(`errors.${code}`, localParams(params)) : null

  const message = (error: unknown, fallbackKey = 'messages.genericError'): string => {
    const status = (error as { status?: number })?.status
    const data = (error as { data?: ApiErrorBody })?.data
    // Throttle do DRF: o corpo vem em inglês ("Request was throttled…").
    if (status === 429) return t('messages.tooManyRequests')
    // 413 sem código nosso: a plataforma cortou o corpo antes de o backend responder.
    if (status === 413 && !(data && typeof data.code === 'string')) return t('messages.payloadTooLarge')
    if (data && typeof data === 'object') {
      if (typeof data.code === 'string') {
        const translated = fromCode(data.code, data.params)
        if (translated) return translated
      }
      // Primeiro procura um código nosso; se não houver, usa a primeira string
      // solta (ex.: validadores nativos de senha, que o DRF já devolve em pt-BR).
      let firstPlain: string | null = null
      for (const [key, value] of Object.entries(data)) {
        const items = Array.isArray(value) ? value : [value]
        for (const item of items) {
          if (typeof item !== 'string') continue
          const translated = fromCode(item)
          if (translated) return translated
          if (key !== 'code' && key !== 'detail' && item && firstPlain === null) firstPlain = item
        }
      }
      if (typeof data.detail === 'string' && data.detail) return data.detail
      if (firstPlain) return firstPlain
    }
    if (!status) return t('messages.networkError')
    return t(fallbackKey)
  }

  // `block_unreadable` traz `title` (nome do exame); laudos antigos só têm a página. O
  // texto de `warnings.block_unreadable` recebe o título já com o travessão, ou vazio.
  const warningText = (warning: { code: string; params?: Record<string, unknown> }): string => {
    if (!te(`warnings.${warning.code}`)) return warning.code
    const params = { ...(warning.params ?? {}) }
    if (warning.code === 'block_unreadable') {
      params.title = params.title ? ` — ${String(params.title)}` : ''
    }
    return t(`warnings.${warning.code}`, params)
  }

  // Erros de validação por campo do DRF: `{value_raw: ['result_value_required']}`. Cada item é um
  // código nosso (traduzido) ou uma mensagem nativa já em pt-BR. Chaves de erro com código
  // (`code`, `params`, `detail`) não são de campo e ficam de fora.
  const fieldErrors = (error: unknown): Record<string, string> => {
    const data = (error as { data?: ApiErrorBody })?.data
    const out: Record<string, string> = {}
    if (!data || typeof data !== 'object') return out
    for (const [field, value] of Object.entries(data)) {
      if (field === 'code' || field === 'params' || field === 'detail') continue
      const first = (Array.isArray(value) ? value : [value]).find((item) => typeof item === 'string')
      if (typeof first === 'string' && first) out[field] = fromCode(first) ?? first
    }
    return out
  }

  // Todos os itens de um campo (ex.: uma senha pode falhar em vários validadores), cada um
  // traduzido pelo código; mensagem nativa do DRF já vem em pt-BR e passa como está.
  const fieldMessages = (error: unknown, field: string): string[] => {
    const value = (error as { data?: ApiErrorBody })?.data?.[field]
    const items = Array.isArray(value) ? value : [value]
    return items
      .filter((item): item is string => typeof item === 'string' && item !== '')
      .map((item) => fromCode(item) ?? item)
  }

  const errorText = (code: string, params?: Record<string, unknown>): string =>
    fromCode(code, params) ?? t('messages.genericError')

  return { message, warningText, errorText, fieldErrors, fieldMessages }
}
