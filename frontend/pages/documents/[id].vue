<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import type { AnalyteOption, ExamDocumentDetail, ExamResult, ExtractionRun } from '@/types/exams'

definePageMeta({ middleware: 'auth' })
const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)
const { t } = useI18n()
const { get, post, patch, del } = useApi()
const fmt = useFormat()
const apiError = useApiError()
const notice = useNotice()

// As três leituras são independentes: disparadas juntas, não em fila. O
// `useResilientAsyncData` mantém o último valor bom se um refresh falhar.
const [
  { data: doc, error: docError, refresh: refreshDoc, status: docStatus },
  { data: results, error: resultsError, refresh: refreshResults, status: resultsStatus },
  { data: analytes },
  { data: runs, error: runsError, refresh: refreshRuns, status: runsStatus },
] = await Promise.all([
  // 404 -> `null` (laudo não encontrado); outros erros sobem para `docError`.
  useResilientAsyncData<ExamDocumentDetail | null>(
    `document-${id}`,
    () => get<ExamDocumentDetail>(`/exam-documents/${id}/`),
    { default: () => null },
  ),
  useResilientAsyncData<ExamResult[]>(
    `document-${id}-results`,
    async () => (await get<ExamResult[]>(`/exam-documents/${id}/results/`)) ?? [],
    { default: () => [] },
  ),
  // Só alimenta o seletor do diálogo de edição: se falhar, o resto da tela segue
  // utilizável (o seletor fica vazio) em vez de derrubar a página.
  useResilientAsyncData<AnalyteOption[]>(
    'analyte-options',
    async () => {
      try {
        return (await get<AnalyteOption[]>('/analytes/')) ?? []
      } catch {
        return []
      }
    },
    { default: () => [] },
  ),
  // Diagnóstico da leitura: complementar. Uma falha aparece só dentro da própria
  // seção (`runsError`), nunca em `loadError`, e o último valor bom é mantido.
  useResilientAsyncData<ExtractionRun[]>(
    `document-${id}-runs`,
    async () => (await get<ExtractionRun[]>(`/exam-documents/${id}/runs/`)) ?? [],
    { default: () => [] },
  ),
])

const loadError = computed(() => Boolean(docError.value || resultsError.value))
const reloading = computed(() => docStatus.value === 'pending' || resultsStatus.value === 'pending')

const refreshAll = () => Promise.all([refreshDoc(), refreshResults(), refreshRuns()])

useHead({ title: () => doc.value?.laboratory_name || t('document.notIdentified') })

// A leitura roda em segundo plano: o WebSocket avisa quando o status muda.
const lastEvent = useDocumentEvent()
const refreshThrottled = useThrottledCall(() => refreshAll())
watch(lastEvent, (event) => {
  if (event?.documentId === id) refreshThrottled()
})

// A resposta do PATCH é o laudo inteiro: atualiza a tela (título da aba incluído) sem refazer as leituras.
const onLaboratorySaved = (updated: ExamDocumentDetail) => {
  doc.value = updated
}

const current = computed(() => results.value.filter((r) => r.origin === 'current'))
const previous = computed(() => results.value.filter((r) => r.origin === 'lab_previous'))
const reviewOnly = ref(false)
const rows = computed(() =>
  reviewOnly.value ? current.value.filter((r) => r.review_status === 'needs_review') : current.value,
)
const processing = computed(() => doc.value?.status === 'extracting' || doc.value?.status === 'uploaded')
const progressDone = computed(() => doc.value?.progress_done ?? 0)
const progressTotal = computed(() => doc.value?.progress_total ?? 0)
const progressPercent = computed(() =>
  progressTotal.value > 0 ? Math.min(100, Math.round((progressDone.value / progressTotal.value) * 100)) : 0,
)

// Rede de segurança: se o WebSocket cair ou um evento se perder, a página ainda
// converge sozinha enquanto a leitura está em andamento.
useProcessingPoll(processing, () => refreshAll())

const editing = ref<ExamResult | null>(null)
const editVisible = computed({
  get: () => editing.value !== null,
  set: (open) => {
    if (!open) editing.value = null
  },
})

// `acting` impede o duplo clique: confirmar/reprocessar duas vezes dispara duas
// requisições (e a segunda costuma falhar com 409 e mostrar um erro enganoso).
const acting = ref(false)
async function run(action: () => Promise<unknown>, successText?: string) {
  if (acting.value) return
  acting.value = true
  try {
    await action()
    if (successText) notice.success(successText)
    await refreshAll()
  } catch (error) {
    notice.error(apiError.message(error))
  } finally {
    acting.value = false
  }
}

const confirmAll = () => run(() => post(`/exam-documents/${id}/confirm/`), t('document.confirmed'))
const reprocess = () => run(() => post(`/exam-documents/${id}/reprocess/`), t('document.reprocessStarted'))
const confirmRow = (row: ExamResult) =>
  run(() => patch(`/exam-results/${row.id}/`, { review_status: 'confirmed' }))

const deleting = ref(false)
const removing = ref(false)
async function removeDocument() {
  if (removing.value) return
  removing.value = true
  try {
    await del(`/exam-documents/${id}/`)
    notice.success(t('documents.deleted'))
    await router.push('/documents')
  } catch (error) {
    // Fecha o diálogo modal: o aviso global ficaria atrás da máscara.
    deleting.value = false
    notice.error(apiError.message(error))
  } finally {
    removing.value = false
  }
}

const valueLabel = (row: ExamResult) => row.value_raw || row.value_text || t('common.empty')
const firstLines = (text: string) => text.split('\n').slice(0, 3).join('\n')
</script>

<template>
  <div v-if="!doc" class="space-y-4">
    <NuxtLink to="/documents" class="inline-flex items-center gap-1 text-sm text-sepia-600 hover:text-sepia-900">
      <Icon icon="mdi:arrow-left" class="h-4 w-4" />
      {{ $t('document.backToList') }}
    </NuxtLink>
    <AppLoadError v-if="loadError" :loading="reloading" @retry="refreshAll" />
    <p v-else class="rounded-lg border border-sepia-200 bg-white p-10 text-center text-sepia-500">
      {{ $t('document.notFound') }}
    </p>
  </div>

  <div v-else class="space-y-6">
    <NuxtLink to="/documents" class="inline-flex items-center gap-1 text-sm text-sepia-600 hover:text-sepia-900">
      <Icon icon="mdi:arrow-left" class="h-4 w-4" />
      {{ $t('document.backToList') }}
    </NuxtLink>

    <AppLoadError v-if="loadError" :loading="reloading" @retry="refreshAll" />

    <header class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <DocumentsLaboratoryEditor
          :document-id="doc.id"
          :name="doc.laboratory_name"
          :disabled="processing"
          @saved="onLaboratorySaved"
        />
        <p class="mt-1 text-sm text-sepia-500">{{ doc.original_filename }}</p>
        <div class="mt-2">
          <DocumentsStatusBadge :status="doc.status" :done="doc.progress_done" :total="doc.progress_total" />
          <!-- Região viva só com a situação: o progresso atualiza em silêncio. -->
          <span class="sr-only" role="status">{{ $t(`status.${doc.status}`) }}</span>
        </div>
      </div>

      <div class="flex flex-wrap gap-2">
        <Button
          v-if="doc.review_count || doc.warnings.length"
          type="button"
          :disabled="acting"
          class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950 disabled:!opacity-50"
          @click="confirmAll"
        >
          {{ $t('document.confirmAll') }}
        </Button>
        <Button
          type="button"
          :disabled="processing || acting"
          :title="$t('document.reprocessHint')"
          class="!rounded-md !border !border-sepia-300 !bg-white !px-4 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50 disabled:!opacity-50"
          @click="reprocess"
        >
          {{ $t('document.reprocess') }}
        </Button>
        <Button
          type="button"
          class="!rounded-md !border !border-vermelho-200 !bg-white !px-4 !py-2 !text-sm !text-vermelho-700 hover:!bg-vermelho-50"
          @click="deleting = true"
        >
          {{ $t('document.delete') }}
        </Button>
      </div>
    </header>

    <section v-if="processing" class="space-y-2 rounded-lg border border-azul-200 bg-azul-50 px-4 py-3 text-sm text-azul-800">
      <p class="flex items-center gap-2">
        <Icon icon="mdi:loading" class="h-4 w-4 animate-spin" />
        <template v-if="progressTotal > 0">
          {{ $t('documents.progressLabel', { done: progressDone, total: progressTotal }) }}
        </template>
        <template v-else>{{ $t('document.processing') }}</template>
      </p>
      <div
        v-if="progressTotal > 0"
        role="progressbar"
        aria-valuemin="0"
        :aria-valuemax="progressTotal"
        :aria-valuenow="progressDone"
        :aria-label="$t('documents.progressBarLabel')"
        class="h-2 overflow-hidden rounded-full bg-azul-100"
      >
        <div class="h-full rounded-full bg-azul-600 transition-all duration-500" :style="{ width: `${progressPercent}%` }" />
      </div>
      <p class="text-xs">{{ $t('documents.extractingHint') }}</p>
    </section>

    <section v-if="doc.status === 'failed'" role="alert" class="rounded-lg border border-vermelho-200 bg-vermelho-50 p-4 text-sm text-vermelho-800">
      <h2 class="font-semibold">{{ $t('document.failedTitle') }}</h2>
      <p class="mt-1">{{ apiError.errorText(doc.error_code, doc.error_params) }}</p>
    </section>

    <section v-if="doc.warnings.length" class="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
      <h2 class="font-semibold">{{ $t('document.warningsTitle') }}</h2>
      <ul class="mt-1 list-disc pl-5">
        <li v-for="warning in doc.warnings" :key="warning.code">{{ apiError.warningText(warning) }}</li>
      </ul>
    </section>

    <dl class="grid grid-cols-2 gap-4 rounded-lg border border-sepia-200 bg-white p-4 shadow-sm md:grid-cols-3 xl:grid-cols-6">
      <div>
        <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.collectedOn') }}</dt>
        <dd class="text-sm font-medium">{{ fmt.date(doc.collected_on) }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.issuedAt') }}</dt>
        <dd class="text-sm font-medium">{{ fmt.date(doc.issued_at) }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.accession') }}</dt>
        <dd class="text-sm font-medium">{{ doc.accession_number || $t('common.empty') }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.physician') }}</dt>
        <dd class="text-sm font-medium">{{ doc.ordering_physician || $t('common.empty') }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.pages') }}</dt>
        <dd class="text-sm font-medium">{{ doc.page_count }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.method') }}</dt>
        <dd class="text-sm font-medium">{{ doc.extraction_method ? $t(`method.${doc.extraction_method}`) : $t('common.empty') }}</dd>
      </div>
    </dl>

    <section class="overflow-hidden rounded-lg border border-sepia-200 bg-white shadow-sm">
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-sepia-100 bg-sepia-50 px-4 py-2">
        <h2 class="text-sm font-semibold text-sepia-800">{{ $t('document.resultsTitle') }}</h2>
        <label class="flex items-center gap-2 text-xs text-sepia-700">
          <input v-model="reviewOnly" type="checkbox" class="h-4 w-4 rounded border-sepia-300">
          {{ $t('document.reviewOnly') }}
        </label>
      </div>

      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="text-left text-xs uppercase tracking-wide text-sepia-500">
            <tr>
              <th class="px-4 py-2">{{ $t('document.colExam') }}</th>
              <th class="px-4 py-2 text-right">{{ $t('document.colValue') }}</th>
              <th class="px-4 py-2">{{ $t('document.colReference') }}</th>
              <th class="px-4 py-2">{{ $t('document.colSituation') }}</th>
              <th class="px-4 py-2">{{ $t('document.colReview') }}</th>
              <th class="px-4 py-2"><span class="sr-only">{{ $t('common.actions') }}</span></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-sepia-100">
            <tr v-if="!rows.length">
              <td colspan="6" class="px-4 py-8 text-center text-sepia-500">{{ $t('document.noResults') }}</td>
            </tr>
            <tr
              v-for="row in rows"
              :key="row.id"
              :class="['align-top', row.review_status === 'needs_review' ? 'bg-amber-50/60' : 'hover:bg-sepia-50']"
            >
              <td class="px-4 py-3">
                <p class="font-medium text-sepia-900">{{ row.name_raw }}</p>
                <NuxtLink
                  v-if="row.analyte_info"
                  :to="`/analytes/${row.analyte_info.slug}`"
                  class="text-xs text-sepia-600 underline decoration-sepia-300"
                >
                  {{ row.analyte_info.name }} · {{ $t('document.viewHistory') }}
                </NuxtLink>
                <span v-else class="text-xs text-amber-700">{{ $t('document.unmapped') }}</span>
                <p v-if="row.section_raw" class="text-xs text-sepia-500">{{ row.section_raw }}</p>
              </td>
              <td class="px-4 py-3 text-right tabular-nums">
                <span class="font-semibold">{{ valueLabel(row) }}</span>
                <span class="ml-1 text-xs text-sepia-500">{{ row.unit || row.unit_raw }}</span>
                <p v-if="row.source_line" class="mt-0.5 max-w-[14rem] truncate text-[11px] text-sepia-500" :title="`${$t('document.sourceLine')}: ${row.source_line}`">
                  {{ row.source_line }}
                </p>
              </td>
              <td class="px-4 py-3 max-w-[20rem] whitespace-pre-line text-xs text-sepia-600" :title="row.reference_raw">
                {{ firstLines(row.reference_raw) || $t('common.empty') }}
              </td>
              <td class="px-4 py-3">
                <ResultsFlagBadge :flag="row.flag" />
                <p v-if="row.interpretation" class="mt-0.5 text-xs text-sepia-500">{{ row.interpretation }}</p>
              </td>
              <td class="px-4 py-3">
                <span class="text-xs text-sepia-600">{{ $t(`reviewStatus.${row.review_status}`) }}</span>
                <ul v-if="row.warnings.length" class="mt-1 space-y-0.5 text-xs text-amber-800">
                  <li v-for="warning in row.warnings" :key="warning.code" class="flex gap-1">
                    <Icon icon="mdi:alert-outline" class="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {{ apiError.warningText(warning) }}
                  </li>
                </ul>
              </td>
              <td class="px-4 py-3 text-right whitespace-nowrap">
                <button
                  v-if="row.review_status === 'needs_review'"
                  type="button"
                  class="rounded p-1 text-sepia-500 hover:bg-sepia-100 hover:text-azul-700"
                  :title="$t('document.confirmRow')"
                  :aria-label="$t('document.confirmRow')"
                  :disabled="acting"
                  @click="confirmRow(row)"
                >
                  <Icon icon="mdi:check" class="h-5 w-5" />
                </button>
                <button
                  type="button"
                  class="rounded p-1 text-sepia-500 hover:bg-sepia-100 hover:text-sepia-900"
                  :aria-label="$t('common.edit')"
                  @click="editing = row"
                >
                  <Icon icon="mdi:pencil-outline" class="h-5 w-5" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <details v-if="previous.length" class="rounded-lg border border-sepia-200 bg-white shadow-sm">
      <summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-sepia-800">
        {{ $t('document.previousTitle') }} ({{ previous.length }})
      </summary>
      <p class="px-4 text-xs text-sepia-500">{{ $t('document.previousHint') }}</p>
      <table class="mt-2 min-w-full text-sm">
        <tbody class="divide-y divide-sepia-100">
          <tr v-for="row in previous" :key="row.id">
            <td class="px-4 py-2">{{ row.analyte_info?.name ?? row.name_raw }}</td>
            <td class="px-4 py-2 whitespace-nowrap text-sepia-600">{{ fmt.date(row.collected_at) }}</td>
            <td class="px-4 py-2 text-right tabular-nums">{{ row.value_raw }} <span class="text-xs text-sepia-500">{{ row.unit }}</span></td>
          </tr>
        </tbody>
      </table>
    </details>

    <details v-if="doc.page_text.length" class="rounded-lg border border-sepia-200 bg-white shadow-sm">
      <summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-sepia-800">{{ $t('document.extractedText') }}</summary>
      <p class="px-4 text-xs text-sepia-500">{{ $t('document.extractedTextHint') }}</p>
      <div v-for="page in doc.page_text" :key="page.number" class="px-4 py-3">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-sepia-500">{{ $t('document.page', { page: page.number }) }}</h3>
        <pre class="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-sepia-50 p-3 font-mono text-xs text-sepia-800">{{ page.lines.join('\n') }}</pre>
      </div>
    </details>

    <DocumentsRunDiagnostics :runs="runs" :error="Boolean(runsError)" :loading="runsStatus === 'pending'" @retry="refreshRuns" />

    <ResultsEditResultDialog v-model:visible="editVisible" :result="editing" :analytes="analytes" @saved="refreshAll" />

    <Dialog v-model:visible="deleting" modal :header="$t('document.deleteTitle')" class="w-[28rem] max-w-[95vw]">
      <p class="text-sm text-sepia-700">{{ $t('document.deleteBody') }}</p>
      <div class="mt-6 flex justify-end gap-2">
        <Button
          type="button"
          class="!rounded-md !border !border-sepia-300 !bg-white !px-4 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50"
          @click="deleting = false"
        >
          {{ $t('common.cancel') }}
        </Button>
        <Button
          type="button"
          :disabled="removing"
          class="!rounded-md !bg-vermelho-600 !px-4 !py-2 !text-sm !font-medium !text-white hover:!bg-vermelho-700 disabled:!opacity-50"
          @click="removeDocument"
        >
          {{ $t('common.delete') }}
        </Button>
      </div>
    </Dialog>
  </div>
</template>
