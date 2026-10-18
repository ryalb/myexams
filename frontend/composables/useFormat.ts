// Formatação pt-BR por vue-i18n (datas 25/08/2026, números 1.234,5).
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  // Campo só-data não tem fuso: `new Date('2026-08-25')` seria meia-noite UTC e
  // renderizaria um dia antes em UTC-3. Monta a data local.
  const match = DATE_ONLY.exec(value)
  if (match) return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

// Para EXIBIR. Os formatos de data do i18n fixam America/Sao_Paulo (carimbos dos
// laudos), então um campo só-data não pode virar "meia-noite local": para quem está
// a leste de Brasília isso cairia no dia anterior. Ao meio-dia UTC, o dia é o mesmo
// em qualquer fuso de uso real.
export function parseDisplayDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const match = DATE_ONLY.exec(value)
  if (match) return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12))
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function toIsoDateString(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function useFormat() {
  const { n, d, t } = useI18n()
  const empty = () => t('common.empty')

  const date = (value: string | null | undefined) => {
    const parsed = parseDisplayDate(value)
    return parsed ? d(parsed, 'short') : empty()
  }
  const dateTime = (value: string | null | undefined) => {
    const parsed = parseDisplayDate(value)
    return parsed ? d(parsed, 'long') : empty()
  }
  const number = (value: number | null | undefined) =>
    value === null || value === undefined ? empty() : n(value, 'decimal')
  const signedNumber = (value: number | null | undefined) =>
    value === null || value === undefined ? empty() : `${value > 0 ? '+' : ''}${n(value, 'decimal')}`
  const signedPercent = (value: number | null | undefined) =>
    value === null || value === undefined ? empty() : `${value > 0 ? '+' : ''}${n(value, 'percent')}%`

  return { date, dateTime, number, signedNumber, signedPercent }
}
