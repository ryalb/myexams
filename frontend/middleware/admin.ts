// Rotas só de administrador (`/admin/*`). Use depois de `auth`: definePageMeta({ middleware: ['auth', 'admin'] }).
// Quem não é administrador (ou perdeu o papel) volta para os laudos; deslogado vai para o login.
export default defineNuxtRouteMiddleware(async () => {
  const { accessToken } = useAuth()
  if (!accessToken.value) return navigateTo('/login')

  // Sempre pergunta ao servidor: o papel pode ter sido retirado depois do login.
  const current = await useCurrentUser().reload()
  if (!current?.is_admin) return navigateTo('/documents')
})
