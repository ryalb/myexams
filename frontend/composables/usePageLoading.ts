// Estado do spinner de pagina (ver `plugins/page-loading.client.ts`).
//
// `readsInFlight` conta as leituras feitas por `useApi` (GET e queries
// GraphQL). Escritas ficam de fora de proposito: salvar um formulario ja tem
// estado proprio no botao, e cobrir a pagina inteira a cada POST esconderia o
// formulario que o usuario acabou de enviar.
export function usePageLoading() {
  const readsInFlight = useState('page-loading:reads-in-flight', () => 0)
  // Comeca `true`: a primeira navegacao do app acontece antes de qualquer
  // `router.beforeEach` de plugin existir, entao ninguem ligaria o spinner.
  const pageLoading = useState('page-loading:active', () => true)

  const trackRead = async <T>(request: () => Promise<T>): Promise<T> => {
    readsInFlight.value += 1
    try {
      return await request()
    } finally {
      readsInFlight.value -= 1
    }
  }

  return { readsInFlight, pageLoading, trackRead }
}
