<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import type { ExamDocumentDetail, Laboratory } from '@/types/exams'

// Nome do laboratório do laudo, com edição em linha. A troca vale só para este laudo
// (o backend reaproveita ou cria o laboratório pelo nome; nunca renomeia o de outros).
const props = defineProps<{
  documentId: number
  name: string
  // A leitura (upload/extração) ainda grava no laudo: o backend recusa a edição.
  disabled: boolean
}>()
const emit = defineEmits<{ saved: [document: ExamDocumentDetail] }>()

const MAX_LENGTH = 255
const { t } = useI18n()
const { get, patch } = useApi()
const apiError = useApiError()
const notice = useNotice()

const editing = ref(false)
const draft = ref('')
const saving = ref(false)
const error = ref('')
const suggestions = ref<Laboratory[]>([])

const editButton = ref<HTMLButtonElement | null>(null)
const input = ref<{ $el: HTMLInputElement } | null>(null)

const inputId = `laboratory-name-${props.documentId}`
const hintId = `${inputId}-hint`
const listId = `${inputId}-list`

const focusInput = async () => {
  await nextTick()
  const element = input.value?.$el
  element?.focus()
  element?.select()
}
const focusButton = async () => {
  await nextTick()
  editButton.value?.focus()
}

// Sugestões só dos laboratórios dos próprios laudos; falhar aqui não impede a edição.
async function loadSuggestions() {
  try {
    suggestions.value = (await get<Laboratory[]>('/laboratories/')) ?? []
  } catch {
    suggestions.value = []
  }
}

function open() {
  if (props.disabled || editing.value) return
  draft.value = props.name
  error.value = ''
  editing.value = true
  focusInput()
  loadSuggestions()
}

function cancel() {
  if (saving.value) return
  editing.value = false
  error.value = ''
  focusButton()
}

async function save() {
  if (saving.value || props.disabled) return
  const name = draft.value.replace(/\s+/g, ' ').trim()
  if (name.length > MAX_LENGTH) {
    error.value = apiError.errorText('laboratory_name_too_long', { max: MAX_LENGTH })
    return
  }
  // Sem mudança não há o que enviar.
  if (name === props.name) {
    cancel()
    return
  }
  saving.value = true
  error.value = ''
  try {
    const updated = await patch<ExamDocumentDetail>(`/exam-documents/${props.documentId}/`, {
      laboratory_name: name,
    })
    if (updated) {
      emit('saved', updated)
      notice.success(name ? t('document.laboratorySaved') : t('document.laboratoryRemoved'))
      editing.value = false
      focusButton()
    }
  } catch (e) {
    error.value = apiError.message(e)
    focusInput()
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <div v-if="!editing" class="flex items-center gap-2">
      <h1 class="font-display text-2xl font-semibold text-sepia-900">
        {{ name || $t('document.notIdentified') }}
      </h1>
      <button
        ref="editButton"
        type="button"
        class="rounded p-1 text-sepia-500 hover:bg-sepia-100 hover:text-sepia-900 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="disabled"
        :aria-label="$t('document.laboratoryEdit')"
        :title="disabled ? $t('document.laboratoryBusy') : $t('document.laboratoryEdit')"
        @click="open"
      >
        <Icon icon="mdi:pencil-outline" class="h-5 w-5" />
      </button>
    </div>

    <form v-else class="max-w-xl space-y-2" @submit.prevent="save" @keydown.esc.prevent="cancel">
      <label :for="inputId" class="block text-sm font-medium text-sepia-700">
        {{ $t('document.laboratoryLabel') }}
      </label>
      <div class="flex flex-wrap items-center gap-2">
        <InputText
          :id="inputId"
          ref="input"
          v-model="draft"
          :list="listId"
          :maxlength="MAX_LENGTH"
          :disabled="saving"
          :invalid="Boolean(error)"
          :aria-describedby="hintId"
          autocomplete="off"
          :placeholder="$t('document.laboratoryPlaceholder')"
          class="min-w-0 flex-1"
        />
        <datalist :id="listId">
          <option v-for="laboratory in suggestions" :key="laboratory.id" :value="laboratory.name" />
        </datalist>
        <Button
          type="submit"
          :disabled="saving || disabled"
          :aria-label="$t('common.save')"
          class="!rounded-md !bg-sepia-900 !px-3 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
        >
          <Icon :icon="saving ? 'mdi:loading' : 'mdi:check'" :class="['h-4 w-4', saving ? 'animate-spin' : '']" />
          {{ saving ? $t('common.saving') : $t('common.save') }}
        </Button>
        <Button
          type="button"
          :disabled="saving"
          :aria-label="$t('common.cancel')"
          class="!rounded-md !border !border-sepia-300 !bg-white !px-3 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50 disabled:!opacity-50"
          @click="cancel"
        >
          <Icon icon="mdi:close" class="h-4 w-4" />
          {{ $t('common.cancel') }}
        </Button>
      </div>
      <p :id="hintId" class="text-xs text-sepia-500">
        {{ disabled ? $t('document.laboratoryBusy') : $t('document.laboratoryHint') }}
      </p>
      <p v-if="error" role="alert" class="rounded-md border border-vermelho-200 bg-vermelho-50 px-3 py-2 text-sm text-vermelho-800">
        {{ error }}
      </p>
    </form>
  </div>
</template>
