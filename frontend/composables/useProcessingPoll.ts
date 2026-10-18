import type { MaybeRefOrGetter } from 'vue'

const POLL_INTERVAL_MS = 5000
const THROTTLE_MS = 1000

/**
 * Rede de segurança para o WebSocket: enquanto `active` for verdadeiro, chama
 * `refresh` a cada 5 s. Se o socket cair ou um evento se perder, a tela ainda
 * converge sozinha.
 *
 * - pausa com a aba oculta e faz um refresh imediato ao voltar a ficar visível;
 * - nunca sobrepõe requisições: se o refresh anterior não terminou, pula o ciclo;
 * - limpa o timer quando `active` vira falso e ao desmontar.
 */
export function useProcessingPoll(
  active: MaybeRefOrGetter<boolean>,
  refresh: () => unknown,
  intervalMs = POLL_INTERVAL_MS,
) {
  let timer: ReturnType<typeof setInterval> | undefined
  let pending = false

  const visible = () => typeof document === 'undefined' || document.visibilityState === 'visible'

  const run = async () => {
    if (pending || !toValue(active) || !visible()) return
    pending = true
    try {
      await refresh()
    } catch {
      // falha transitória: o próximo ciclo tenta de novo
    } finally {
      pending = false
    }
  }

  const stop = () => {
    clearInterval(timer)
    timer = undefined
  }

  const start = () => {
    if (timer === undefined) timer = setInterval(run, intervalMs)
  }

  const onVisibilityChange = () => {
    if (visible()) run()
  }

  watch(
    () => toValue(active),
    (isActive) => (isActive ? start() : stop()),
    { immediate: true },
  )

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  onScopeDispose(() => {
    stop()
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  })
}

/**
 * Limita `fn` a uma chamada por `waitMs` (com chamada final se houve eventos no
 * intervalo). O backend emite um evento por bloco lido; sem isso cada um geraria
 * uma requisição.
 */
export function useThrottledCall(fn: () => unknown, waitMs = THROTTLE_MS) {
  let last = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  const fire = () => {
    timer = undefined
    last = Date.now()
    fn()
  }

  onScopeDispose(() => clearTimeout(timer))

  return () => {
    const remaining = waitMs - (Date.now() - last)
    if (remaining <= 0) {
      clearTimeout(timer)
      fire()
    } else if (timer === undefined) {
      timer = setTimeout(fire, remaining)
    }
  }
}
