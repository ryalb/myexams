<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'

// Troca da própria senha. Com senha temporária (definida por um administrador) o bloco avisa e
// o resto do app fica atrás dele até a troca (ver `middleware/auth.ts`).
const { t } = useI18n()
const { post } = useApi()
const { setTokens } = useAuth()
const apiError = useApiError()
const notice = useNotice()
const router = useRouter()
const currentUser = useCurrentUser()

const form = reactive({ current: '', next: '', repeat: '' })
const saving = ref(false)
const showPasswords = ref(false)
const currentError = ref('')
const newErrors = ref<string[]>([])
const repeatError = ref('')
const generalError = ref('')

const mustChange = computed(() => currentUser.mustChangePassword)

const currentInput = ref<{ $el: HTMLInputElement } | null>(null)
const newInput = ref<{ $el: HTMLInputElement } | null>(null)
const repeatInput = ref<{ $el: HTMLInputElement } | null>(null)

// Chegando pelo redirecionamento da senha temporária, o cursor já fica no primeiro campo. A
// página fica escondida sob o spinner enquanto carrega: só depois dele dá para focar.
async function focusFirstField() {
  await nextTick()
  document.getElementById('change-password')?.scrollIntoView?.({ block: 'start' })
  currentInput.value?.$el.focus()
}

const { pageLoading } = usePageLoading()
onMounted(() => {
  if (!mustChange.value) return
  if (!pageLoading.value) {
    focusFirstField()
    return
  }
  const stop = watch(pageLoading, (loading) => {
    if (loading) return
    stop()
    focusFirstField()
  })
})

function clearErrors() {
  currentError.value = ''
  newErrors.value = []
  repeatError.value = ''
  generalError.value = ''
}

async function submit() {
  if (saving.value) return
  clearErrors()
  if (form.next !== form.repeat) {
    repeatError.value = t('profile.passwordMismatch')
    repeatInput.value?.$el.focus()
    return
  }
  saving.value = true
  const forced = mustChange.value
  try {
    const tokens = await post<{ access: string; refresh: string }>('/auth/change-password/', {
      current_password: form.current,
      new_password: form.next,
    })
    if (tokens) {
      // A troca invalida os tokens antigos: guarda o par novo antes de qualquer outra chamada.
      setTokens(tokens)
      Object.assign(form, { current: '', next: '', repeat: '' })
      await currentUser.reload()
      notice.success(t('profile.passwordChanged'))
      if (forced) await router.push('/documents')
    }
  } catch (e) {
    const code = (e as { data?: { code?: string } })?.data?.code
    const fromNew = apiError.fieldMessages(e, 'new_password')
    if (code === 'current_password_incorrect') {
      currentError.value = apiError.message(e)
      currentInput.value?.$el.focus()
    } else if (fromNew.length) {
      newErrors.value = fromNew
      newInput.value?.$el.focus()
    } else {
      generalError.value = apiError.message(e)
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section
    id="change-password"
    class="mt-6 rounded-lg border border-sepia-200 bg-white p-6 shadow-sm"
    aria-labelledby="change-password-title"
  >
    <h2 id="change-password-title" class="font-display text-lg font-semibold text-sepia-900">
      {{ $t('profile.passwordTitle') }}
    </h2>

    <p
      v-if="mustChange"
      role="alert"
      class="mt-3 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
    >
      <Icon icon="mdi:alert-outline" class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      {{ $t('profile.passwordMustChange') }}
    </p>
    <p v-else class="mt-1 text-sm text-sepia-500">{{ $t('profile.passwordSubtitle') }}</p>

    <form class="mt-4 space-y-4" novalidate @submit.prevent="submit">
      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="password-current">
          {{ $t('profile.passwordCurrent') }}
        </label>
        <InputText
          id="password-current"
          ref="currentInput"
          v-model="form.current"
          :type="showPasswords ? 'text' : 'password'"
          autocomplete="current-password"
          required
          :invalid="Boolean(currentError)"
          :aria-invalid="Boolean(currentError)"
          :aria-describedby="currentError ? 'password-current-error' : undefined"
          class="w-full"
        />
        <p v-if="currentError" id="password-current-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          {{ currentError }}
        </p>
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="password-new">
          {{ $t('profile.passwordNew') }}
        </label>
        <InputText
          id="password-new"
          ref="newInput"
          v-model="form.next"
          :type="showPasswords ? 'text' : 'password'"
          autocomplete="new-password"
          required
          :invalid="newErrors.length > 0"
          :aria-invalid="newErrors.length > 0"
          :aria-describedby="newErrors.length ? 'password-new-error' : undefined"
          class="w-full"
        />
        <ul v-if="newErrors.length" id="password-new-error" role="alert" class="mt-1 space-y-0.5 text-xs text-vermelho-700">
          <li v-for="message in newErrors" :key="message">{{ message }}</li>
        </ul>
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="password-repeat">
          {{ $t('profile.passwordRepeat') }}
        </label>
        <InputText
          id="password-repeat"
          ref="repeatInput"
          v-model="form.repeat"
          :type="showPasswords ? 'text' : 'password'"
          autocomplete="new-password"
          required
          :invalid="Boolean(repeatError)"
          :aria-invalid="Boolean(repeatError)"
          :aria-describedby="repeatError ? 'password-repeat-error' : undefined"
          class="w-full"
        />
        <p v-if="repeatError" id="password-repeat-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          {{ repeatError }}
        </p>
      </div>

      <label class="flex items-center gap-2 text-sm text-sepia-700">
        <input v-model="showPasswords" type="checkbox" class="h-4 w-4 rounded border-sepia-300">
        {{ $t('profile.passwordShow') }}
      </label>

      <p v-if="generalError" role="alert" class="rounded-md border border-vermelho-200 bg-vermelho-50 px-3 py-2 text-sm text-vermelho-800">
        {{ generalError }}
      </p>

      <Button
        type="submit"
        :disabled="saving || !form.current || !form.next || !form.repeat"
        class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
      >
        {{ saving ? $t('common.saving') : $t('profile.passwordSubmit') }}
      </Button>
    </form>
  </section>
</template>
