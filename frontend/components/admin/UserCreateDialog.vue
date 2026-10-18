<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import type { AdminUserInput, AdminUserWithSecret } from '@/types/accounts'

// Cria uma conta. Sem senha digitada o servidor gera uma temporária, devolvida uma única vez
// (o pai a mostra em `AdminTemporaryPasswordDialog`). A conta nasce com troca de senha pendente.
const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ created: [result: AdminUserWithSecret] }>()

const { post } = useApi()
const apiError = useApiError()

type Field = 'name' | 'email' | 'password'
const FIELD_ORDER: Field[] = ['email', 'name', 'password']
const INPUT_ID: Record<Field, string> = {
  name: 'new-user-name',
  email: 'new-user-email',
  password: 'new-user-password',
}

const form = reactive({ name: '', email: '', isAdmin: false, password: '' })
const showPassword = ref(false)
const saving = ref(false)
const fieldErrors = ref<Partial<Record<Field, string[]>>>({})
// O diálogo é modal: um aviso global ficaria atrás da máscara. Erros sem campo aparecem aqui.
const generalError = ref('')
// Quem abriu o diálogo: o foco volta para lá ao fechar.
let opener: HTMLElement | null = null

watch(
  visible,
  (open) => {
    if (!open) return
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    Object.assign(form, { name: '', email: '', isAdmin: false, password: '' })
    showPassword.value = false
    fieldErrors.value = {}
    generalError.value = ''
  },
  { flush: 'sync' },
)

const focusField = (field: Field) => document.getElementById(INPUT_ID[field])?.focus()
const onShow = () => focusField('email')
const onHide = () => {
  // Não deixa a senha digitada na memória do componente depois de fechar.
  form.password = ''
  if (opener?.isConnected) opener.focus()
  opener = null
}

const describedBy = (field: Field, hint?: string) =>
  [fieldErrors.value[field]?.length ? `${INPUT_ID[field]}-error` : '', hint ?? ''].filter(Boolean).join(' ') || undefined

async function save() {
  if (saving.value) return
  saving.value = true
  fieldErrors.value = {}
  generalError.value = ''
  const body: AdminUserInput = {
    email: form.email.trim(),
    name: form.name.trim(),
    is_admin: form.isAdmin,
  }
  // Vazio = "gere uma senha temporária": o campo nem vai na requisição.
  if (form.password) body.password = form.password
  try {
    const created = await post<AdminUserWithSecret>('/admin/users/', body)
    if (created) {
      visible.value = false
      emit('created', created)
    }
  } catch (e) {
    const next: Partial<Record<Field, string[]>> = {}
    for (const field of FIELD_ORDER) {
      const messages = apiError.fieldMessages(e, field)
      if (messages.length) next[field] = messages
    }
    fieldErrors.value = next
    const first = FIELD_ORDER.find((field) => next[field])
    // Erro de campo conhecido vai no campo; qualquer outro (limite de requisições...) no aviso.
    generalError.value = first ? '' : apiError.message(e)
    await nextTick()
    if (first) focusField(first)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    modal
    :header="$t('admin.createTitle')"
    class="w-[30rem] max-w-[95vw]"
    @show="onShow"
    @after-hide="onHide"
  >
    <form class="space-y-4" novalidate @submit.prevent="save">
      <p class="text-sm text-sepia-600">{{ $t('admin.createIntro') }}</p>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="new-user-email">
          {{ $t('admin.fieldEmail') }} <span class="text-vermelho-700" aria-hidden="true">*</span>
          <span class="sr-only">({{ $t('common.required') }})</span>
        </label>
        <InputText
          id="new-user-email"
          v-model="form.email"
          type="email"
          maxlength="150"
          autocomplete="off"
          required
          :invalid="Boolean(fieldErrors.email)"
          :aria-invalid="Boolean(fieldErrors.email)"
          :aria-describedby="describedBy('email')"
          class="w-full"
        />
        <ul v-if="fieldErrors.email" id="new-user-email-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          <li v-for="message in fieldErrors.email" :key="message">{{ message }}</li>
        </ul>
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="new-user-name">
          {{ $t('admin.fieldName') }}
        </label>
        <InputText
          id="new-user-name"
          v-model="form.name"
          maxlength="150"
          autocomplete="off"
          :invalid="Boolean(fieldErrors.name)"
          :aria-invalid="Boolean(fieldErrors.name)"
          :aria-describedby="describedBy('name')"
          class="w-full"
        />
        <ul v-if="fieldErrors.name" id="new-user-name-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          <li v-for="message in fieldErrors.name" :key="message">{{ message }}</li>
        </ul>
      </div>

      <label class="flex items-start gap-2 text-sm text-sepia-700">
        <input v-model="form.isAdmin" type="checkbox" class="mt-0.5 h-4 w-4 rounded border-sepia-300">
        <span>
          {{ $t('admin.fieldRole') }}
          <span class="block text-xs text-sepia-500">{{ $t('admin.fieldRoleHint') }}</span>
        </span>
      </label>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="new-user-password">
          {{ $t('admin.fieldPassword') }}
        </label>
        <div class="relative">
          <InputText
            id="new-user-password"
            v-model="form.password"
            :type="showPassword ? 'text' : 'password'"
            maxlength="128"
            autocomplete="new-password"
            :invalid="Boolean(fieldErrors.password)"
            :aria-invalid="Boolean(fieldErrors.password)"
            :aria-describedby="describedBy('password', 'new-user-password-hint')"
            class="w-full !pr-10"
          />
          <button
            type="button"
            class="absolute top-1/2 right-2 -translate-y-1/2 text-sepia-500 hover:text-sepia-700"
            :aria-label="showPassword ? $t('common.hidePassword') : $t('common.showPassword')"
            @click="showPassword = !showPassword"
          >
            <Icon :icon="showPassword ? 'mdi:eye-off-outline' : 'mdi:eye-outline'" class="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <p id="new-user-password-hint" class="mt-1 text-xs text-sepia-500">{{ $t('admin.fieldPasswordHint') }}</p>
        <ul v-if="fieldErrors.password" id="new-user-password-error" role="alert" class="mt-1 space-y-0.5 text-xs text-vermelho-700">
          <li v-for="message in fieldErrors.password" :key="message">{{ message }}</li>
        </ul>
      </div>

      <p v-if="generalError" role="alert" class="rounded-md border border-vermelho-200 bg-vermelho-50 px-3 py-2 text-sm text-vermelho-800">
        {{ generalError }}
      </p>

      <div class="flex justify-end gap-2">
        <Button
          type="button"
          :disabled="saving"
          class="!rounded-md !border !border-sepia-300 !bg-white !px-4 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50 disabled:!opacity-50"
          @click="visible = false"
        >
          {{ $t('common.cancel') }}
        </Button>
        <Button
          type="submit"
          :disabled="saving || !form.email.trim()"
          class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
        >
          {{ saving ? $t('common.saving') : $t('admin.createSubmit') }}
        </Button>
      </div>
    </form>
  </Dialog>
</template>
