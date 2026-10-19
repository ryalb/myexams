import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import { MOBILE_BREAKPOINT, defaultHidden, parseHidden, toggleHidden } from '@/utils/columns'
import type { ColumnDef } from '@/utils/columns'

const STORAGE_PREFIX = 'myexams:columns:'

// Escolha de colunas visíveis de uma tabela, guardada neste navegador (só a lista de
// colunas escondidas; nenhum dado de exame). O app é SPA (`ssr: false`), então ler o
// `localStorage` já na criação não causa divergência de hidratação.
export function useColumnVisibility(id: string, columns: Ref<ColumnDef[]>) {
  const storageKey = `${STORAGE_PREFIX}${id}`
  const mobile = import.meta.client && window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`).matches

  const read = () => {
    try {
      return parseHidden(localStorage.getItem(storageKey), columns.value)
    } catch {
      return null // armazenamento bloqueado (navegação privada, política do navegador)
    }
  }

  const stored = ref<string[] | null>(import.meta.client ? read() : null)
  const hidden = computed(() => stored.value ?? defaultHidden(columns.value, mobile))
  const hiddenSet = computed(() => new Set(hidden.value))

  const persist = () => {
    try {
      if (stored.value === null) localStorage.removeItem(storageKey)
      else localStorage.setItem(storageKey, JSON.stringify(stored.value))
    } catch {
      /* sem armazenamento: a escolha vale só até recarregar */
    }
  }

  return {
    columns,
    /** `true` se a coluna deve aparecer. */
    show: (key: string) => !hiddenSet.value.has(key),
    /** Quantas colunas aparecem (para o `colspan` das linhas de aviso). */
    visibleCount: computed(() => columns.value.filter((column) => !hiddenSet.value.has(column.key)).length),
    customized: computed(() => stored.value !== null),
    toggle(key: string) {
      stored.value = toggleHidden(hidden.value, key, columns.value)
      persist()
    },
    reset() {
      stored.value = null
      persist()
    },
  }
}

export type ColumnVisibility = ReturnType<typeof useColumnVisibility>
