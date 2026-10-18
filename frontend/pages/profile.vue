<script setup lang="ts">
import Button from 'primevue/button'
import DatePicker from 'primevue/datepicker'
import Select from 'primevue/select'
import { parseDate, toIsoDateString } from '@/composables/useFormat'

definePageMeta({ middleware: 'auth' })
const { t } = useI18n()
const { get, put } = useApi()
const apiError = useApiError()
const notice = useNotice()

interface Profile {
  sex: string
  birth_date: string | null
}

useHead({ title: () => t('profile.title') })

const { data: profile, error, refresh, status } = await useResilientAsyncData<Profile | null>(
  'profile',
  () => get<Profile>('/profile/'),
  { default: () => null },
)

// `sex` é `null` quando o usuário limpa o campo (botão de limpar do Select).
const form = reactive({
  sex: (profile.value?.sex ?? '') as string | null,
  birthDate: parseDate(profile.value?.birth_date),
})

// Preenche o formulário uma única vez, quando o perfil chega (inclusive depois de
// "Tentar novamente"). Refreshes seguintes não podem sobrescrever o que o usuário
// está digitando.
let populated = Boolean(profile.value)
watch(profile, (loaded) => {
  if (!loaded || populated) return
  populated = true
  form.sex = loaded.sex ?? ''
  form.birthDate = parseDate(loaded.birth_date)
})
const saving = ref(false)

const sexOptions = computed(() => [
  { value: 'male', label: t('sex.male') },
  { value: 'female', label: t('sex.female') },
])

async function save() {
  // Sem perfil carregado o formulário está em branco: salvar sobrescreveria os
  // dados reais com vazios.
  if (!profile.value || saving.value) return
  saving.value = true
  try {
    await put('/profile/', {
      // O DRF rejeita `null` em campo de texto; vazio = não informado.
      sex: form.sex ?? '',
      birth_date: form.birthDate ? toIsoDateString(form.birthDate) : null,
    })
    notice.success(t('profile.saved'))
  } catch (error) {
    notice.error(apiError.message(error))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-xl">
    <h1 class="font-display text-2xl font-semibold text-sepia-900">{{ $t('profile.title') }}</h1>
    <p class="mt-1 text-sm text-sepia-500">{{ $t('profile.subtitle') }}</p>

    <AppLoadError v-if="error" :loading="status === 'pending'" class="mt-4" @retry="refresh" />

    <form class="mt-6 space-y-5 rounded-lg border border-sepia-200 bg-white p-6 shadow-sm" @submit.prevent="save">
      <div>
        <label id="profile-sex-label" class="mb-1 block text-sm font-medium text-sepia-700" for="profile-sex">{{ $t('profile.sex') }}</label>
        <Select
          v-model="form.sex"
          input-id="profile-sex"
          aria-labelledby="profile-sex-label"
          :disabled="!profile"
          :options="sexOptions"
          option-label="label"
          option-value="value"
          show-clear
          :placeholder="$t('profile.sexPlaceholder')"
          class="w-full"
        />
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="profile-birth">
          {{ $t('profile.birthDate') }}
        </label>
        <DatePicker v-model="form.birthDate" input-id="profile-birth" :disabled="!profile" show-icon date-format="dd/mm/yy" :max-date="new Date()" class="w-full" />
      </div>

      <p class="text-xs text-sepia-500">{{ $t('profile.why') }}</p>

      <Button
        type="submit"
        :disabled="saving || !profile"
        class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
      >
        {{ saving ? $t('common.saving') : $t('common.save') }}
      </Button>
    </form>

    <ProfileChangePasswordCard />
  </div>
</template>
