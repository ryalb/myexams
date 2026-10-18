<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import type { AnalyteOption, ExamResult } from '@/types/exams'

const props = defineProps<{ result: ExamResult | null; analytes: AnalyteOption[] }>()
const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ saved: [] }>()

const { t } = useI18n()
const { patch } = useApi()
const apiError = useApiError()
const notice = useNotice()

const form = reactive({ analyte: null as number | null, value_raw: '', unit_raw: '', reference_raw: '' })
const saving = ref(false)
// O diálogo é modal: um aviso global ficaria atrás da máscara. O erro aparece aqui dentro.
const error = ref('')

watch(
  () => [props.result, visible.value],
  () => {
    error.value = ''
    if (!props.result) return
    form.analyte = props.result.analyte
    form.value_raw = props.result.value_raw
    form.unit_raw = props.result.unit_raw
    form.reference_raw = props.result.reference_raw
  },
  { immediate: true },
)

async function save() {
  if (!props.result || saving.value) return
  saving.value = true
  error.value = ''
  try {
    await patch(`/exam-results/${props.result.id}/`, { ...form })
    notice.success(t('document.saved'))
    visible.value = false
    emit('saved')
  } catch (e) {
    error.value = apiError.message(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal :header="$t('document.editTitle')" class="w-[32rem] max-w-[95vw]">
    <form class="space-y-4" @submit.prevent="save">
      <div>
        <p class="text-xs font-medium uppercase tracking-wide text-sepia-500">{{ $t('document.editName') }}</p>
        <p class="text-sm text-sepia-900">{{ result?.name_raw }}</p>
      </div>

      <div>
        <label id="edit-analyte-label" class="mb-1 block text-sm font-medium text-sepia-700" for="edit-analyte">
          {{ $t('document.editExam') }}
        </label>
        <Select
          v-model="form.analyte"
          input-id="edit-analyte"
          aria-labelledby="edit-analyte-label"
          :options="analytes"
          option-label="name"
          option-value="id"
          filter
          show-clear
          :placeholder="$t('document.editExamPlaceholder')"
          class="w-full"
        />
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="mb-1 block text-sm font-medium text-sepia-700" for="edit-value">
            {{ $t('document.editValue') }}
          </label>
          <InputText id="edit-value" v-model="form.value_raw" class="w-full" />
        </div>
        <div>
          <label class="mb-1 block text-sm font-medium text-sepia-700" for="edit-unit">
            {{ $t('document.editUnit') }}
          </label>
          <InputText id="edit-unit" v-model="form.unit_raw" class="w-full" />
        </div>
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="edit-reference">
          {{ $t('document.editReference') }}
        </label>
        <Textarea id="edit-reference" v-model="form.reference_raw" rows="4" class="w-full" />
      </div>

      <p class="text-xs text-sepia-500">{{ $t('document.editHint') }}</p>

      <p v-if="error" role="alert" class="rounded-md border border-vermelho-200 bg-vermelho-50 px-3 py-2 text-sm text-vermelho-800">
        {{ error }}
      </p>

      <div class="flex justify-end gap-2">
        <Button
          type="button"
          class="!rounded-md !border !border-sepia-300 !bg-white !px-4 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50"
          @click="visible = false"
        >
          {{ $t('common.cancel') }}
        </Button>
        <Button
          type="submit"
          :disabled="saving"
          class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
        >
          {{ saving ? $t('common.saving') : $t('common.save') }}
        </Button>
      </div>
    </form>
  </Dialog>
</template>
