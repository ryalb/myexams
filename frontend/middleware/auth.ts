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

  // Senha temporária (definida por um administrador): até trocá-la, o único destino é o bloco
  // "Trocar senha" do perfil. É conveniência de tela; a API não depende disso.
  if (accessToken.value && !publicRoutes.includes(to.path) && to.path !== '/profile') {
    const current = await useCurrentUser().fetchUser()
    if (current?.must_change_password) {
      return navigateTo({ path: '/profile', hash: '#change-password' })
    }
  }
})
