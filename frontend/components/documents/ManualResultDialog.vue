<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import type { AnalyteOption, ExamResult, ManualResultInput } from '@/types/exams'

// Digita um resultado que a leitura automática não pegou. O servidor deriva número, unidade
// canônica, faixa, situação e avisos: aqui só vai o que está impresso no laudo.
const props = defineProps<{
  documentId: number
  // Data de coleta do laudo (AAAA-MM-DD): vem preenchida e pode ser trocada.
  defaultDate: string | null
  // Nome do bloco não lido que originou a inclusão (vazio na inclusão avulsa).
  initialName: string
  analytes: AnalyteOption[]
}>()
const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ saved: [result: ExamResult] }>()

const { t } = useI18n()
const { post } = useApi()
const apiError = useApiError()
const notice = useNotice()

type Field = 'name_raw' | 'value_raw' | 'unit_raw' | 'reference_raw' | 'collected_at' | 'notes' | 'analyte'
const FIELD_ORDER: Field[] = ['name_raw', 'analyte', 'value_raw', 'unit_raw', 'reference_raw', 'collected_at', 'notes']
const INPUT_ID: Record<Field, string> = {
  name_raw: 'manual-name',
  analyte: 'manual-analyte',
  value_raw: 'manual-value',
  unit_raw: 'manual-unit',
  reference_raw: 'manual-reference',
  collected_at: 'manual-collected',
  notes: 'manual-notes',
}

const form = reactive({
  name_raw: '',
  analyte: null as number | null,
  value_raw: '',
  unit_raw: '',
  reference_raw: '',
  collected_on: '',
  notes: '',
})
const saving = ref(false)
const fieldErrors = ref<Partial<Record<Field, string>>>({})
// O diálogo é modal: um aviso global ficaria atrás da máscara. Erros sem campo aparecem aqui dentro.
const generalError = ref('')
// Exame do catálogo escolhido pelo nome digitado (e não pelo seletor): se o nome deixar de
// casar, ele sai. Um exame escolhido no seletor nunca é trocado pelo nome.
let autoAnalyte: number | null = null
// Quem abriu o diálogo: o foco volta para lá ao fechar.
let opener: HTMLElement | null = null

const normalized = (text: string) => text.trim().toLocaleLowerCase('pt-BR')
const byName = computed(() => new Map(props.analytes.map((a) => [normalized(a.name), a.id])))

function focusField(field: Field) {
  const element = document.getElementById(INPUT_ID[field])
  element?.focus()
}

watch(
  visible,
  (open) => {
    if (!open) return
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    form.name_raw = props.initialName
    form.analyte = null
    form.value_raw = ''
    form.unit_raw = ''
    form.reference_raw = ''
    form.collected_on = props.defaultDate ?? ''
    form.notes = ''
    fieldErrors.value = {}
    generalError.value = ''
    autoAnalyte = null
    if (form.name_raw) selectByName()
  },
  { flush: 'sync' },
)

function selectByName() {
  const match = byName.value.get(normalized(form.name_raw))
  if (match !== undefined) {
    form.analyte = match
    autoAnalyte = match
  } else if (autoAnalyte !== null && form.analyte === autoAnalyte) {
    form.analyte = null
    autoAnalyte = null
  }
}

watch(() => form.name_raw, selectByName)

// Escolher o exame do catálogo com o nome vazio já preenche o nome.
watch(
  () => form.analyte,
  (id) => {
    if (id !== autoAnalyte) autoAnalyte = null
    if (id === null || id === autoAnalyte || form.name_raw.trim()) return
    form.name_raw = props.analytes.find((a) => a.id === id)?.name ?? ''
  },
)

// Com o nome já preenchido (bloco não lido), o cursor vai direto para o valor.
const onShow = () => focusField(props.initialName ? 'value_raw' : 'name_raw')
const onHide = () => {
  if (opener?.isConnected) opener.focus()
  opener = null
}

async function save() {
  if (saving.value) return
  saving.value = true
  fieldErrors.value = {}
  generalError.value = ''
  const body: ManualResultInput = {
    document: props.documentId,
    name_raw: form.name_raw,
    analyte: form.analyte,
    value_raw: form.value_raw,
    unit_raw: form.unit_raw,
    reference_raw: form.reference_raw,
    collected_at: form.collected_on || null,
    notes: form.notes,
  }
  try {
    const created = await post<ExamResult>('/exam-results/', body)
    if (created) {
      notice.success(t('document.manualSaved'))
      visible.value = false
      emit('saved', created)
    }
  } catch (e) {
    const errors = apiError.fieldErrors(e) as Partial<Record<Field | 'document', string>>
    const known = FIELD_ORDER.filter((field) => errors[field])
    const next: Partial<Record<Field, string>> = {}
    for (const field of known) next[field] = errors[field]
    fieldErrors.value = next
    // Erro de campo conhecido vai no campo; qualquer outro (duplicado, laudo em leitura...) no aviso.
    generalError.value = known.length ? '' : apiError.message(e)
    await nextTick()
    const first = known[0]
    if (first) focusField(first)
  } finally {
    saving.value = false
  }
}

const describedBy = (field: Field, hint?: string) =>
  [fieldErrors.value[field] ? `${INPUT_ID[field]}-error` : '', hint ?? ''].filter(Boolean).join(' ') || undefined
</script>

<template>
  <Dialog
    v-model:visible="visible"
    modal
    :header="$t('document.manualTitle')"
    class="w-[34rem] max-w-[95vw]"
    @show="onShow"
    @after-hide="onHide"
  >
    <form class="space-y-4" novalidate @submit.prevent="save">
      <p class="text-sm text-sepia-600">{{ $t('document.manualIntro') }}</p>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="manual-name">
          {{ $t('document.manualName') }} <span class="text-vermelho-700" aria-hidden="true">*</span>
          <span class="sr-only">({{ $t('common.required') }})</span>
        </label>
        <InputText
          id="manual-name"
          v-model="form.name_raw"
          list="manual-name-list"
          maxlength="255"
          autocomplete="off"
          required
          :invalid="Boolean(fieldErrors.name_raw)"
          :aria-invalid="Boolean(fieldErrors.name_raw)"
          :aria-describedby="describedBy('name_raw', 'manual-name-hint')"
          class="w-full"
        />
        <datalist id="manual-name-list">
          <option v-for="analyte in analytes" :key="analyte.id" :value="analyte.name" />
        </datalist>
        <p id="manual-name-hint" class="mt-1 text-xs text-sepia-500">{{ $t('document.manualNameHint') }}</p>
        <p v-if="fieldErrors.name_raw" id="manual-name-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          {{ fieldErrors.name_raw }}
        </p>
      </div>

      <div>
        <label id="manual-analyte-label" class="mb-1 block text-sm font-medium text-sepia-700" for="manual-analyte">
          {{ $t('document.manualExam') }}
        </label>
        <Select
          v-model="form.analyte"
          input-id="manual-analyte"
          aria-labelledby="manual-analyte-label"
          :options="analytes"
          option-label="name"
          option-value="id"
          filter
          show-clear
          :placeholder="$t('document.manualExamPlaceholder')"
          class="w-full"
        />
        <p v-if="fieldErrors.analyte" id="manual-analyte-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          {{ fieldErrors.analyte }}
        </p>
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="mb-1 block text-sm font-medium text-sepia-700" for="manual-value">
            {{ $t('document.manualValue') }} <span class="text-vermelho-700" aria-hidden="true">*</span>
            <span class="sr-only">({{ $t('common.required') }})</span>
          </label>
          <InputText
            id="manual-value"
            v-model="form.value_raw"
            maxlength="255"
            autocomplete="off"
            required
            :invalid="Boolean(fieldErrors.value_raw)"
            :aria-invalid="Boolean(fieldErrors.value_raw)"
            :aria-describedby="describedBy('value_raw', 'manual-value-hint')"
            class="w-full"
          />
          <p id="manual-value-hint" class="mt-1 text-xs text-sepia-500">{{ $t('document.manualValueHint') }}</p>
          <p v-if="fieldErrors.value_raw" id="manual-value-error" role="alert" class="mt-1 text-xs text-vermelho-700">
            {{ fieldErrors.value_raw }}
          </p>
        </div>
        <div>
          <label class="mb-1 block text-sm font-medium text-sepia-700" for="manual-unit">
            {{ $t('document.manualUnit') }}
          </label>
          <InputText
            id="manual-unit"
            v-model="form.unit_raw"
            maxlength="60"
            autocomplete="off"
            :invalid="Boolean(fieldErrors.unit_raw)"
            :aria-invalid="Boolean(fieldErrors.unit_raw)"
            :aria-describedby="describedBy('unit_raw')"
            class="w-full"
          />
          <p v-if="fieldErrors.unit_raw" id="manual-unit-error" role="alert" class="mt-1 text-xs text-vermelho-700">
            {{ fieldErrors.unit_raw }}
          </p>
        </div>
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="manual-reference">
          {{ $t('document.manualReference') }}
        </label>
        <Textarea
          id="manual-reference"
          v-model="form.reference_raw"
          rows="3"
          :invalid="Boolean(fieldErrors.reference_raw)"
          :aria-invalid="Boolean(fieldErrors.reference_raw)"
          :aria-describedby="describedBy('reference_raw')"
          class="w-full"
        />
        <p v-if="fieldErrors.reference_raw" id="manual-reference-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          {{ fieldErrors.reference_raw }}
        </p>
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="manual-collected">
          {{ $t('document.manualCollected') }}
        </label>
        <InputText
          id="manual-collected"
          v-model="form.collected_on"
          type="date"
          :invalid="Boolean(fieldErrors.collected_at)"
          :aria-invalid="Boolean(fieldErrors.collected_at)"
          :aria-describedby="describedBy('collected_at')"
          class="w-full"
        />
        <p v-if="fieldErrors.collected_at" id="manual-collected-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          {{ fieldErrors.collected_at }}
        </p>
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-sepia-700" for="manual-notes">
          {{ $t('document.manualNotes') }}
        </label>
        <Textarea
          id="manual-notes"
          v-model="form.notes"
          rows="2"
          :invalid="Boolean(fieldErrors.notes)"
          :aria-invalid="Boolean(fieldErrors.notes)"
          :aria-describedby="describedBy('notes')"
          class="w-full"
        />
        <p v-if="fieldErrors.notes" id="manual-notes-error" role="alert" class="mt-1 text-xs text-vermelho-700">
          {{ fieldErrors.notes }}
        </p>
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
          :disabled="saving"
          class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
        >
          {{ saving ? $t('common.saving') : $t('common.save') }}
        </Button>
      </div>
    </form>
  </Dialog>
</template>
