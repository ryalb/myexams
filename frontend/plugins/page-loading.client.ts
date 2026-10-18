import { nextTick, watch } from 'vue'
import { usePageLoading } from '@/composables/usePageLoading'

// Spinner de pagina: liga ao trocar de rota e so desliga quando (1) a pagina
// nova terminou de montar e (2) nao ha mais leitura do backend em andamento.
//
// (1) vem do hook `page:loading:end` do Nuxt -- cobre as paginas que fazem
// `await useFetch` no setup (accounts, cards, integrations), cujas requisicoes
// nao passam por `useApi` e portanto nao entram em `readsInFlight`.
// (2) cobre o resto: composables com `useAsyncData` disparam as leituras no
// setup sem bloquear a renderizacao.
//
// Mudanca so de query (filtro/paginacao em /transactions) nao liga o spinner:
// a pagina continua montada e tem seu proprio indicador; recarregar a tela
// inteira a cada filtro seria pior. Pelo mesmo motivo, refresh em segundo
// plano (`useRealtimeSync` -> `refreshNuxtData`) nunca liga o spinner, so
// atrasa o desligamento se coincidir com uma navegacao.

// Uma leitura costuma disparar outra logo em seguida (ex.: benchmarks de
// mercado dependem das datas das posicoes): espera um pouco com o contador em
// zero antes de esconder, para o spinner nao piscar entre as duas.
const SETTLE_DELAY_MS = 80

// Rede de seguranca: se algum hook nao disparar (erro na pagina, request
// pendurado), o usuario nao fica preso olhando o spinner para sempre.
const MAX_SPINNER_MS = 20000

export default defineNuxtPlugin((nuxtApp) => {
  const router = useRouter()
  const { readsInFlight, pageLoading } = usePageLoading()

  let waitingForPage = true
  let settleTimer: ReturnType<typeof setTimeout> | undefined
  let maxTimer: ReturnType<typeof setTimeout> | undefined

  const finish = () => {
    clearTimeout(settleTimer)
    clearTimeout(maxTimer)
    pageLoading.value = false
  }

  const isLocalOnlyRoute = (route = router.currentRoute.value) =>
    Boolean(route?.meta?.skipPageLoading)

  const scheduleSettle = () => {
    clearTimeout(settleTimer)
    if (isLocalOnlyRoute()) {
      finish()
      return
    }
    if (!pageLoading.value || waitingForPage || readsInFlight.value > 0) return
    settleTimer = setTimeout(() => {
      if (!waitingForPage && readsInFlight.value === 0) finish()
    }, SETTLE_DELAY_MS)
  }

  const start = () => {
    waitingForPage = true
    pageLoading.value = true
    clearTimeout(settleTimer)
    clearTimeout(maxTimer)
    maxTimer = setTimeout(finish, MAX_SPINNER_MS)
  }

  if (isLocalOnlyRoute()) {
    waitingForPage = false
    finish()
  } else {
    maxTimer = setTimeout(finish, MAX_SPINNER_MS)
  }

  router.beforeEach((to, from) => {
    if (to.meta?.skipPageLoading) {
      waitingForPage = false
      finish()
      return
    }
    if (to.path !== from.path) start()
  })

  const markPageReady = async () => {
    // Deixa o setup da pagina nova rodar e disparar as leituras antes de olhar
    // o contador -- senao ele ainda estaria em zero.
    await nextTick()
    waitingForPage = false
    scheduleSettle()
  }

  // Navegacao cancelada/redirecionada pelo middleware tambem cai aqui.
  nuxtApp.hook('page:loading:end', markPageReady)
  nuxtApp.hook('page:finish', markPageReady)
  nuxtApp.hook('app:mounted', markPageReady)

  watch(readsInFlight, scheduleSettle)
})
