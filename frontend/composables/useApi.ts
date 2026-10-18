import { usePageLoading } from '@/composables/usePageLoading'

// Envelope do PageNumberPagination do DRF. Só as coleções que crescem sem limite
// (laudos, resultados) vêm assim; as listas pequenas seguem como array puro.
export type Paginated<T> = {
  count: number
  next: string | null
  previous: string | null
  results: T[]
}

type JsonBody = Record<string, unknown> | unknown[]

export function useApi() {
  const config = useRuntimeConfig()
  const router = useRouter()
  const { accessToken: token, logout, refresh } = useAuth()
  const { trackRead } = usePageLoading()

  const getHeaders = (): Record<string, string> => {
    const base: Record<string, string> = {}
    if (token.value) {
      base.Authorization = `Bearer ${token.value}`
    }
    return base
  }

  const handleUnauthorized = () => {
    const path = router.currentRoute.value.path
    const isAuthPage = path === '/login' || path === '/register'
    if (!isAuthPage) {
      logout()
      router.push('/login')
    }
  }

  // Um 401 quase sempre é o access token expirando no meio da sessão — não a
  // sessão em si. Tenta renovar e refazer a chamada uma vez antes de deslogar.
  const isUnauthorized = (error: unknown) => (error as { status?: number })?.status === 401

  const isNotFound = (error: unknown) => (error as { status?: number })?.status === 404

  // Só o 404 vira `null` ("não existe"). Qualquer outro erro (500, rede) é
  // propagado: devolver `null` aqui faria uma falha de servidor aparecer como
  // "laudo não encontrado" ou "nenhum resultado ainda".
  const notFoundOrThrow = (error: unknown): null => {
    if (isNotFound(error)) return null
    throw error
  }

  const get = <T>(path: string): Promise<T | null> => trackRead(() => getUntracked<T>(path))

  const getUntracked = async <T>(path: string): Promise<T | null> => {
    const attempt = () => $fetch<T>(`${config.public.apiBaseUrl}${path}`, { headers: getHeaders() })

    try {
      return await attempt()
    } catch (error) {
      if (!isUnauthorized(error)) return notFoundOrThrow(error)
      if (await refresh()) {
        try {
          return await attempt()
        } catch (retryError) {
          if (isUnauthorized(retryError)) {
            handleUnauthorized()
            return null
          }
          return notFoundOrThrow(retryError)
        }
      }
      handleUnauthorized()
      return null
    }
  }

  // Escritas propagam o erro depois de tratar o 401. Devolver `null` numa falha
  // de validação faz o formulário parecer ter salvo.
  //
  // `FormData` (upload) NÃO leva `Content-Type`: o navegador define o
  // `multipart/form-data` com o boundary. Forçar JSON aqui quebraria o envio.
  const write = async <T>(
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
    path: string,
    body?: JsonBody | FormData,
  ): Promise<T | null> => {
    const attempt = () =>
      $fetch<T>(`${config.public.apiBaseUrl}${path}`, {
        method,
        headers:
          body && !(body instanceof FormData)
            ? { ...getHeaders(), 'Content-Type': 'application/json' }
            : getHeaders(),
        body,
      })

    try {
      return await attempt()
    } catch (error) {
      if (!isUnauthorized(error)) throw error
      if (await refresh()) {
        try {
          return await attempt()
        } catch (retryError) {
          if (isUnauthorized(retryError)) {
            handleUnauthorized()
            return null
          }
          throw retryError
        }
      }
      handleUnauthorized()
      return null
    }
  }

  const post = <T>(path: string, body?: JsonBody) => write<T>('POST', path, body)
  const put = <T>(path: string, body: JsonBody) => write<T>('PUT', path, body)
  const patch = <T>(path: string, body: JsonBody) => write<T>('PATCH', path, body)
  const del = <T>(path: string) => write<T>('DELETE', path)
  const upload = <T>(path: string, form: FormData) => write<T>('POST', path, form)

  // Queries GraphQL são leituras: contam para o spinner de página como os GETs.
  const graphql = <T>(query: string, variables?: Record<string, unknown>): Promise<T | null> =>
    trackRead(() => graphqlUntracked<T>(query, variables))

  const graphqlUntracked = async <T>(
    query: string,
    variables?: Record<string, unknown>,
  ): Promise<T | null> => {
    const attempt = async () => {
      const response = await $fetch<T>(config.public.graphqlEndpoint as string, {
        method: 'POST',
        headers: { ...getHeaders(), 'Content-Type': 'application/json' },
        body: variables ? { query, variables } : { query },
      })
      // GraphQL responde 200 mesmo com erro de execução: sem `data` e com `errors`
      // é falha, não "lista vazia".
      const body = response as { data?: unknown; errors?: unknown[] } | null
      if (body && !body.data && Array.isArray(body.errors) && body.errors.length) {
        throw Object.assign(new Error('graphql_error'), { data: { errors: body.errors } })
      }
      return response
    }

    try {
      return await attempt()
    } catch (error) {
      if (!isUnauthorized(error)) throw error
      if (await refresh()) {
        try {
          return await attempt()
        } catch (retryError) {
          if (isUnauthorized(retryError)) {
            handleUnauthorized()
            return null
          }
          throw retryError
        }
      }
      handleUnauthorized()
      return null
    }
  }

  return { get, post, put, patch, del, upload, graphql, getHeaders }
}
