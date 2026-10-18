// Módulo (não closure): várias composables chamam refresh() no mesmo instante
// quando o access token expira (várias requests em paralelo levam 401 juntas).
// Sem isso cada uma dispara sua própria /auth/token/refresh/.
let refreshPromise: Promise<boolean> | null = null

export function useAuth() {
  const config = useRuntimeConfig()
  const nuxtApp = useNuxtApp()
  // `strict`: o token só acompanha requisições originadas no próprio site. Os dois
  // cookies precisam das mesmas opções para o navegador tratá-los igual.
  const accessToken = useCookie<string | null>('access_token', { sameSite: 'strict' })
  const refreshToken = useCookie<string | null>('refresh_token', { sameSite: 'strict' })

  const isAuthenticated = computed(() => !!accessToken.value)

  const login = async (username: string, password: string) => {
    const data = await $fetch<{ access: string; refresh: string }>(
      `${config.public.apiBaseUrl}/auth/token/`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: { username, password },
      },
    )

    accessToken.value = data.access
    refreshToken.value = data.refresh
    return data
  }

  const logout = () => {
    accessToken.value = null
    refreshToken.value = null
    // Dados em cache (laudos, resultados) não podem aparecer para quem entrar a
    // seguir na mesma sessão da SPA. `runWithContext`: logout também roda em
    // callbacks assíncronos, onde o contexto do Nuxt já se perdeu.
    nuxtApp.runWithContext(() => clearNuxtData())
  }

  const refresh = (): Promise<boolean> => {
    if (!refreshToken.value) {
      logout()
      return Promise.resolve(false)
    }
    if (refreshPromise) return refreshPromise

    refreshPromise = (async () => {
      try {
        const data = await $fetch<{ access: string }>(
          `${config.public.apiBaseUrl}/auth/token/refresh/`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: { refresh: refreshToken.value },
          },
        )
        accessToken.value = data.access
        return true
      } catch {
        logout()
        return false
      } finally {
        refreshPromise = null
      }
    })()

    return refreshPromise
  }

  return {
    accessToken,
    refreshToken,
    isAuthenticated,
    login,
    logout,
    refresh,
  }
}
