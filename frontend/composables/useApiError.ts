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

  const warningText = (warning: { code: string; params?: Record<string, unknown> }): string =>
    te(`warnings.${warning.code}`)
      ? t(`warnings.${warning.code}`, warning.params ?? {})
      : warning.code

  const errorText = (code: string, params?: Record<string, unknown>): string =>
    fromCode(code, params) ?? t('messages.genericError')

  return { message, warningText, errorText }
}
