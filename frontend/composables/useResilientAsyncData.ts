import type { WatchSource } from 'vue'

/**
 * `useAsyncData` que não joga fora o que já foi carregado quando um refresh falha.
 *
 * O Nuxt, ao falhar, redefine `data` para o `default`. Num refresh em segundo plano
 * (evento do WebSocket) isso trocaria uma tela cheia por "nenhum laudo" / "laudo não
 * encontrado". Aqui o `default` devolve o último valor carregado com sucesso; o
 * `error` continua exposto para a tela mostrar o aviso de falha por cima dos dados.
 */
export function useResilientAsyncData<T>(
  key: string,
  handler: () => Promise<T>,
  options: { default: () => T, watch?: WatchSource<unknown>[] },
) {
  let lastGood = options.default()
  return useAsyncData(
    key,
    async () => {
      lastGood = await handler()
      return lastGood
    },
    { default: () => lastGood, watch: options.watch },
  )
}
