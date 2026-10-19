import { computed, reactive } from 'vue'

interface Profile {
  sex: string
  birth_date: string | null
}

export interface CurrentUser {
  id: number
  email: string
  first_name: string
  last_name: string
  // Papel de administrador do app (gerencia contas; não vê exames de ninguém).
  is_admin: boolean
  // Senha temporária definida por um administrador: precisa ser trocada.
  must_change_password: boolean
  profile: Profile
  // Limites do servidor que o cliente confere antes de enviar.
  limits?: { max_upload_mb?: number }
}

// Uma busca por vez, mesmo com a barra lateral e o middleware pedindo ao mesmo tempo.
let inflight: Promise<CurrentUser | null> | null = null

export function useCurrentUser() {
  // Compartilhado entre a barra lateral, os middlewares e as páginas. Quem faz login ou
  // logout zera (ver `useAuth`), então nunca sobra o usuário anterior.
  const user = useState<CurrentUser | null>('current-user', () => null)
  const loading = useState('current-user:loading', () => false)

  const load = (): Promise<CurrentUser | null> => {
    if (inflight) return inflight
    loading.value = true
    const { accessToken } = useAuth()
    const tokenAtStart = accessToken.value
    inflight = (async () => {
      try {
        const data = await useApi().get<CurrentUser>('/auth/me/')
        // Houve logout/login enquanto a resposta vinha: ela é de outra pessoa.
        if (data && accessToken.value === tokenAtStart) user.value = data
        return data
      } catch {
        // O nome na barra lateral é cosmético: uma falha aqui não deve derrubar a
        // página. `user` fica como estava e a barra mostra só o menu.
        return null
      } finally {
        loading.value = false
        inflight = null
      }
    })()
    return inflight
  }

  // Usa o que já está em memória.
  const fetchUser = async () => (user.value ? user.value : load())
  // Sempre pergunta ao servidor (papel e troca de senha podem ter mudado).
  const reload = () => load()

  const fullName = computed(() => {
    if (!user.value) return ''
    const { first_name, last_name } = user.value
    return [first_name, last_name].filter(Boolean).join(' ') || user.value.email
  })

  const initials = computed(() => {
    if (!user.value) return '?'
    const { first_name, last_name, email } = user.value
    if (first_name || last_name) {
      return [first_name, last_name]
        .filter(Boolean)
        .map((n) => n[0])
        .join('')
        .toUpperCase()
        .slice(0, 2)
    }
    return email.slice(0, 2).toUpperCase()
  })

  const isAdmin = computed(() => Boolean(user.value?.is_admin))
  const mustChangePassword = computed(() => Boolean(user.value?.must_change_password))

  return reactive({ user, loading, fetchUser, reload, fullName, initials, isAdmin, mustChangePassword })
}
