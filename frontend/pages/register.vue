<script setup lang="ts">
import Button from 'primevue/button'

definePageMeta({ layout: false, middleware: 'auth', skipPageLoading: true })
const { t } = useI18n()
useHead({ title: () => t('auth.registerTitle') })
const router = useRouter()
const { login } = useAuth()
const { post } = useApi()
const apiError = useApiError()
const { pageLoading } = usePageLoading()
pageLoading.value = false

const form = reactive({ first_name: '', last_name: '', email: '', password: '' })
const error = ref('')
const loading = ref(false)

const handleSubmit = async () => {
  if (!form.email || !form.password) {
    error.value = t('auth.fillEmailAndPassword')
    return
  }
  loading.value = true
  error.value = ''
  try {
    await post('/auth/register/', { ...form })
    await login(form.email, form.password)
    router.push('/profile')
  } catch (e) {
    error.value = apiError.message(e)
  } finally {
    loading.value = false
  }
}

const fields = [
  { key: 'first_name', labelKey: 'auth.firstName', type: 'text', autocomplete: 'given-name' },
  { key: 'last_name', labelKey: 'auth.lastName', type: 'text', autocomplete: 'family-name' },
  { key: 'email', labelKey: 'auth.email', type: 'email', autocomplete: 'email' },
  { key: 'password', labelKey: 'auth.password', type: 'password', autocomplete: 'new-password' },
] as const
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-sepia-50 px-6 py-12">
    <div class="w-full max-w-sm">
      <img src="/logo.png" :alt="$t('app.name')" class="mx-auto mb-4 h-14 w-auto">
      <p class="text-xs font-medium uppercase tracking-widest text-sepia-500">{{ $t('auth.registerEyebrow') }}</p>
      <h1 class="font-display mt-1 text-3xl font-semibold text-sepia-900">{{ $t('auth.registerTitle') }}</h1>
      <p class="mt-2 text-sm text-sepia-500">{{ $t('auth.registerSubtitle') }}</p>

      <form class="mt-8 space-y-5" novalidate @submit.prevent="handleSubmit">
        <div v-for="field in fields" :key="field.key">
          <label class="mb-1.5 block text-sm font-medium text-sepia-700" :for="`reg-${field.key}`">
            {{ $t(field.labelKey) }}
          </label>
          <input
            :id="`reg-${field.key}`"
            v-model="form[field.key]"
            :type="field.type"
            :autocomplete="field.autocomplete"
            class="h-11 w-full rounded-none border-0 border-b-2 border-sepia-200 bg-transparent px-1 text-sm text-sepia-900 focus:border-sepia-500 focus:outline-none focus:ring-0"
          >
        </div>

        <p v-if="error" role="alert" class="text-sm text-vermelho-600">{{ error }}</p>

        <Button
          type="submit"
          :disabled="loading"
          class="!flex !w-full !items-center !justify-center !rounded-md !bg-sepia-900 !px-4 !py-3 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
        >
          {{ loading ? $t('auth.submittingRegister') : $t('auth.submitRegister') }}
        </Button>
      </form>

      <p class="mt-6 text-center text-sm text-sepia-600">
        {{ $t('auth.haveAccount') }}
        <NuxtLink to="/login" class="font-medium text-sepia-800 underline decoration-sepia-300 hover:text-sepia-950">
          {{ $t('auth.signIn') }}
        </NuxtLink>
      </p>
    </div>
  </div>
</template>
