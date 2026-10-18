import { computed, reactive, ref } from 'vue'

interface Profile {
  sex: string
  birth_date: string | null
}

interface CurrentUser {
  id: number
  email: string
  first_name: string
  last_name: string
  profile: Profile
}

export function useCurrentUser() {
  const user = ref<CurrentUser | null>(null)
  const loading = ref(false)

  const fetchUser = async () => {
    if (user.value) return user.value
    loading.value = true
    try {
      const data = await useApi().get<CurrentUser>('/auth/me/')
      if (data) user.value = data
      return data
    } catch {
      // O nome na barra lateral é cosmético: uma falha aqui não deve derrubar a
      // página. `user` fica nulo e a barra mostra só o menu.
      return null
    } finally {
      loading.value = false
    }
  }

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

  return reactive({ user, loading, fetchUser, fullName, initials })
}
