const publicRoutes = ['/login', '/register']

export default defineNuxtRouteMiddleware(async (to) => {
  const { accessToken, refreshToken, refresh } = useAuth()

  // Sem access token: só vale tentar recuperar a sessão se há refresh token.
  if (!accessToken.value && !publicRoutes.includes(to.path)) {
    const restored = refreshToken.value ? await refresh() : false
    if (!restored) {
      return navigateTo('/login')
    }
  }

  if (accessToken.value && publicRoutes.includes(to.path)) {
    return navigateTo('/documents')
  }
})
