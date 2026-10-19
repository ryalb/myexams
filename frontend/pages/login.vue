<script setup lang="ts">
import Button from 'primevue/button'

definePageMeta({ layout: false, middleware: 'auth', skipPageLoading: true })
const { t } = useI18n()
useHead({ title: () => t('auth.loginTitle') })
const apiError = useApiError()
const router = useRouter()
const { login } = useAuth()
const { pageLoading } = usePageLoading()
pageLoading.value = false

const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const showPassword = ref(false)

const handleSubmit = async () => {
  if (!email.value || !password.value) {
    error.value = t('auth.fillEmailAndPassword')
    return
  }
  loading.value = true
  error.value = ''
  try {
    await login(email.value, password.value)
    router.push('/documents')
  } catch (e) {
    // 400/401 = credenciais erradas. 429 (limite de tentativas), rede fora do ar e
    // 5xx têm mensagem própria: "credenciais inválidas" seria enganoso.
    const status = (e as { status?: number })?.status
    error.value = status === 400 || status === 401 ? t('auth.invalidCredentials') : apiError.message(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-dvh w-full bg-sepia-50">
    <div class="hidden lg:flex lg:w-[44%] flex-col justify-between bg-sepia-950 p-12 text-sepia-100">
      <NuxtLink to="/" class="flex items-center gap-3">
        <img src="/logo.png" :alt="$t('app.name')" class="h-9 w-auto">
        <span class="font-display text-xl font-semibold text-sepia-50">{{ $t('app.name') }}</span>
      </NuxtLink>

      <div class="max-w-md">
        <p class="mb-3 text-xs font-medium uppercase tracking-widest text-sepia-400">{{ $t('app.tagline') }}</p>
        <h2 class="font-display text-3xl font-semibold leading-tight text-sepia-50">{{ $t('auth.coverTitle') }}</h2>
        <p class="mt-4 text-sm leading-relaxed text-sepia-300">{{ $t('auth.coverText') }}</p>
        <ul class="mt-8 space-y-3 border-t border-sepia-800 pt-6 text-sm text-sepia-300">
          <li v-for="key in ['coverPointOne', 'coverPointTwo', 'coverPointThree']" :key="key" class="flex items-center gap-2">
            <Icon icon="mdi:check" class="h-4 w-4 text-azul-300" />
            {{ $t(`auth.${key}`) }}
          </li>
        </ul>
      </div>
      <span />
    </div>

    <div class="flex flex-1 items-center justify-center px-6 py-12 sm:px-12">
      <div class="w-full max-w-sm">
        <p class="text-xs font-medium uppercase tracking-widest text-sepia-500">{{ $t('auth.loginEyebrow') }}</p>
        <h1 class="font-display mt-1 text-3xl font-semibold text-sepia-900">{{ $t('auth.loginTitle') }}</h1>
        <p class="mt-2 text-sm text-sepia-500">{{ $t('auth.loginSubtitle') }}</p>

        <form class="mt-8 space-y-6" novalidate @submit.prevent="handleSubmit">
          <div>
            <label class="mb-1.5 block text-sm font-medium text-sepia-700" for="login-email">
              {{ $t('auth.email') }} <span class="text-vermelho-500" :title="$t('common.required')">*</span>
            </label>
            <input
              id="login-email"
              v-model="email"
              type="email"
              autocomplete="username"
              :placeholder="$t('auth.emailPlaceholder')"
              class="h-11 w-full rounded-none border-0 border-b-2 border-sepia-200 bg-transparent px-1 text-base text-sepia-900 sm:text-sm placeholder:text-sepia-500 focus:border-sepia-500 focus:outline-none focus:ring-0"
            >
          </div>

          <div>
            <label class="mb-1.5 block text-sm font-medium text-sepia-700" for="login-password">
              {{ $t('auth.password') }} <span class="text-vermelho-500" :title="$t('common.required')">*</span>
            </label>
            <div class="relative">
              <input
                id="login-password"
                v-model="password"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="current-password"
                :placeholder="$t('auth.passwordPlaceholder')"
                class="h-11 w-full rounded-none border-0 border-b-2 border-sepia-200 bg-transparent px-1 pr-9 text-base text-sepia-900 sm:text-sm placeholder:text-sepia-500 focus:border-sepia-500 focus:outline-none focus:ring-0"
              >
              <button
                type="button"
                class="absolute top-1/2 right-0 -translate-y-1/2 p-2 text-sepia-500 hover:text-sepia-700"
                :aria-label="showPassword ? $t('common.hidePassword') : $t('common.showPassword')"
                @click="showPassword = !showPassword"
              >
                <Icon :icon="showPassword ? 'mdi:eye-off-outline' : 'mdi:eye-outline'" class="h-5 w-5" />
              </button>
            </div>
          </div>

          <p v-if="error" role="alert" class="text-sm text-vermelho-600">{{ error }}</p>

          <Button
            type="submit"
            :disabled="loading"
            class="!flex !w-full !items-center !justify-center !rounded-md !bg-sepia-900 !px-4 !py-3 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
          >
            {{ loading ? $t('auth.submittingLogin') : $t('auth.submitLogin') }}
          </Button>
        </form>

        <p class="mt-6 text-center text-sm text-sepia-600">
          {{ $t('auth.noAccount') }}
          <NuxtLink to="/register" class="font-medium text-sepia-800 underline decoration-sepia-300 hover:text-sepia-950">
            {{ $t('auth.signUp') }}
          </NuxtLink>
        </p>
      </div>
    </div>
  </div>
</template>
