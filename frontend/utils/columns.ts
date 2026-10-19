// Visibilidade de colunas das tabelas. Funções puras (testadas com `bun test`);
// o estado e o armazenamento ficam em `composables/useColumnVisibility.ts`.

export interface ColumnDef {
  key: string
  /** Texto já traduzido, mostrado na lista de colunas. */
  label: string
  /** Coluna que identifica a linha: não pode ser escondida e nem aparece na lista. */
  required?: boolean
  /** Começa escondida em telas estreitas quando o usuário ainda não escolheu nada. */
  mobileHidden?: boolean
}

/** Largura (px) a partir da qual a tela deixa de ser tratada como celular. */
export const MOBILE_BREAKPOINT = 640

const isHideable = (column: ColumnDef) => !column.required

/** Colunas escondidas quando não há escolha salva. */
export function defaultHidden(columns: readonly ColumnDef[], mobile: boolean): string[] {
  return mobile ? columns.filter((column) => isHideable(column) && column.mobileHidden).map((column) => column.key) : []
}

/**
 * Lê o que foi salvo. Devolve `null` (usar o padrão) se não há nada ou se o texto está
 * corrompido; descarta chaves que não existem mais e as de colunas obrigatórias.
 */
export function parseHidden(raw: string | null, columns: readonly ColumnDef[]): string[] | null {
  if (!raw) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (!Array.isArray(parsed)) return null
  const hideable = new Set(columns.filter(isHideable).map((column) => column.key))
  return [...new Set(parsed.filter((key): key is string => typeof key === 'string' && hideable.has(key)))]
}

/** Liga/desliga uma coluna. Coluna obrigatória ou desconhecida não muda nada. */
export function toggleHidden(hidden: readonly string[], key: string, columns: readonly ColumnDef[]): string[] {
  const column = columns.find((candidate) => candidate.key === key)
  if (!column || !isHideable(column)) return [...hidden]
  return hidden.includes(key) ? hidden.filter((candidate) => candidate !== key) : [...hidden, key]
}
