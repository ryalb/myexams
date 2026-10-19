<script setup lang="ts">
import Button from 'primevue/button'
import DatePicker from 'primevue/datepicker'
import Dialog from 'primevue/dialog'
import Select from 'primevue/select'
import { toIsoDateString } from '@/composables/useFormat'
import type { ExamDocument } from '@/types/exams'

definePageMeta({ middleware: 'auth' })
const { t } = useI18n()
useHead({ title: () => t('documents.title') })
const fmt = useFormat()
const { del } = useApi()
const apiError = useApiError()
const notice = useNotice()

const filters = reactive({
  status: '' as string,
  outOfRange: false,
  from: null as Date | null,
  to: null as Date | null,
})

const { items, count, page, totalPages, hasNext, hasPrevious, next, previous, refresh, refreshAfterRemoval, error, loading } =
  usePaginatedList<ExamDocument>('documents', '/exam-documents/', {
    pageSize: 25,
    params: () => ({
      status: filters.status,
      out_of_range: filters.outOfRange ? '1' : '',
      collected_from: filters.from ? toIsoDateString(filters.from) : '',
      collected_to: filters.to ? toIsoDateString(filters.to) : '',
    }),
  })

const statusOptions = computed(() =>
  ['uploaded', 'extracting', 'needs_review', 'completed', 'failed'].map((value) => ({
    value,
    label: t(`status.${value}`),
  })),
)
const hasFilters = computed(() => Boolean(filters.status || filters.outOfRange || filters.from || filters.to))
const clearFilters = () => Object.assign(filters, { status: '', outOfRange: false, from: null, to: null })

// Eventos do WebSocket: enquanto algum laudo está sendo lido, a lista acompanha.
const lastEvent = useDocumentEvent()
const refreshThrottled = useThrottledCall(() => refresh())
watch(lastEvent, () => refreshThrottled())

// Rede de segurança: se o WebSocket cair ou um evento se perder, a lista ainda
// converge sozinha enquanto algum laudo está sendo lido.
const isProcessing = (status: string) => status === 'uploaded' || status === 'extracting'
const anyProcessing = computed(() => items.value.some((doc) => isProcessing(doc.status)))
const anyExtracting = computed(() => items.value.some((doc) => doc.status === 'extracting'))
useProcessingPoll(anyProcessing, () => refresh())

const cols = useColumnVisibility(
  'documents',
  computed(() => [
    { key: 'collected', label: t('documents.colCollected'), required: true },
    { key: 'laboratory', label: t('documents.colLaboratory') },
    { key: 'file', label: t('documents.colFile'), mobileHidden: true },
    { key: 'status', label: t('documents.colStatus') },
    { key: 'results', label: t('documents.colResults') },
    { key: 'review', label: t('documents.colReview'), mobileHidden: true },
    { key: 'outOfRange', label: t('documents.colOutOfRange'), mobileHidden: true },
    { key: 'uploaded', label: t('documents.colUploaded'), mobileHidden: true },
  ]),
)

const warningSummary = (doc: ExamDocument) => doc.warnings.map((warning) => apiError.warningText(warning)).join('\n')

const toDelete = ref<ExamDocument | null>(null)
const confirmingDelete = computed({
  get: () => toDelete.value !== null,
  set: (open) => {
    if (!open) toDelete.value = null
  },
})

const removing = ref(false)
async function removeDocument() {
  if (!toDelete.value || removing.value) return
  removing.value = true
  try {
    await del(`/exam-documents/${toDelete.value.id}/`)
    notice.success(t('documents.deleted'))
    toDelete.value = null
    await refreshAfterRemoval()
  } catch (e) {
    // Fecha o diálogo modal: o aviso global ficaria atrás da máscara.
    toDelete.value = null
    notice.error(apiError.message(e))
  } finally {
    removing.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <header>
      <h1 class="font-display text-2xl font-semibold text-sepia-900">{{ $t('documents.title') }}</h1>
      <p class="mt-1 text-sm text-sepia-500">{{ $t('documents.subtitle') }}</p>
    </header>

    <DocumentsUploadDropzone @uploaded="refresh" />

    <AppLoadError v-if="error" :loading="loading" @retry="refresh" />

    <p v-if="anyExtracting" class="flex items-start gap-2 rounded-lg border border-azul-200 bg-azul-50 px-4 py-3 text-xs text-azul-800">
      <Icon icon="mdi:information-outline" class="mt-0.5 h-4 w-4 shrink-0" />
      {{ $t('documents.extractingHint') }}
    </p>

    <section class="rounded-lg border border-sepia-200 bg-white shadow-sm">
      <div class="flex flex-wrap items-end gap-4 border-b border-sepia-100 p-4">
        <div class="w-full sm:w-auto">
          <label id="filter-status-label" class="mb-1 block text-xs font-medium text-sepia-600" for="filter-status">{{ $t('documents.filterStatus') }}</label>
          <Select
            v-model="filters.status"
            input-id="filter-status"
            aria-labelledby="filter-status-label"
            :options="statusOptions"
            option-label="label"
            option-value="value"
            show-clear
            :placeholder="$t('common.all')"
            class="w-full sm:w-48"
          />
        </div>
        <div class="w-[calc(50%-0.5rem)] sm:w-auto">
          <label class="mb-1 block text-xs font-medium text-sepia-600" for="filter-from">{{ $t('documents.filterFrom') }}</label>
          <DatePicker v-model="filters.from" input-id="filter-from" date-format="dd/mm/yy" show-button-bar class="w-full sm:w-40" />
        </div>
        <div class="w-[calc(50%-0.5rem)] sm:w-auto">
          <label class="mb-1 block text-xs font-medium text-sepia-600" for="filter-to">{{ $t('documents.filterTo') }}</label>
          <DatePicker v-model="filters.to" input-id="filter-to" date-format="dd/mm/yy" show-button-bar class="w-full sm:w-40" />
        </div>
        <label class="flex items-center gap-2 pb-2 text-sm text-sepia-700">
          <input v-model="filters.outOfRange" type="checkbox" class="h-4 w-4 rounded border-sepia-300">
          {{ $t('documents.filterOutOfRange') }}
        </label>
        <button v-if="hasFilters" type="button" class="pb-2 text-sm text-sepia-600 underline" @click="clearFilters">
          {{ $t('documents.clearFilters') }}
        </button>
        <p class="ml-auto pb-2 text-sm text-sepia-500">{{ $t('documents.countSummary', { count }, count) }}</p>
        <ColumnToggle id="documents" :cols="cols" />
      </div>

      <div class="overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="bg-sepia-50 text-left text-xs uppercase tracking-wide text-sepia-500">
            <tr>
              <th class="px-4 py-3">{{ $t('documents.colCollected') }}</th>
              <th v-if="cols.show('laboratory')" class="px-4 py-3">{{ $t('documents.colLaboratory') }}</th>
              <th v-if="cols.show('file')" class="px-4 py-3">{{ $t('documents.colFile') }}</th>
              <th v-if="cols.show('status')" class="px-4 py-3">{{ $t('documents.colStatus') }}</th>
              <th v-if="cols.show('results')" class="px-4 py-3 text-right">{{ $t('documents.colResults') }}</th>
              <th v-if="cols.show('review')" class="px-4 py-3 text-right">{{ $t('documents.colReview') }}</th>
              <th v-if="cols.show('outOfRange')" class="px-4 py-3 text-right">{{ $t('documents.colOutOfRange') }}</th>
              <th v-if="cols.show('uploaded')" class="px-4 py-3">{{ $t('documents.colUploaded') }}</th>
              <th class="px-4 py-3"><span class="sr-only">{{ $t('common.actions') }}</span></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-sepia-100">
            <tr v-if="!items.length && !error">
              <td :colspan="cols.visibleCount.value + 1" class="px-4 py-10 text-center text-sepia-500">
                {{ hasFilters ? $t('documents.emptyFiltered') : $t('documents.empty') }}
              </td>
            </tr>
            <tr v-for="doc in items" :key="doc.id" class="hover:bg-sepia-50">
              <td class="px-4 py-3 whitespace-nowrap">
                <NuxtLink :to="`/documents/${doc.id}`" class="font-medium text-sepia-800 underline decoration-sepia-300">
                  {{ fmt.date(doc.collected_on) }}
                </NuxtLink>
              </td>
              <td v-if="cols.show('laboratory')" class="px-4 py-3">{{ doc.laboratory_name || $t('common.empty') }}</td>
              <td v-if="cols.show('file')" class="px-4 py-3 max-w-[16rem] truncate" :title="doc.original_filename">{{ doc.original_filename }}</td>
              <td v-if="cols.show('status')" class="px-4 py-3">
                <div class="flex items-center gap-1.5">
                  <DocumentsStatusBadge :status="doc.status" :done="doc.progress_done" :total="doc.progress_total" />
                  <Icon
                    v-if="doc.status === 'needs_review' && doc.warnings.length"
                    icon="mdi:alert-outline"
                    class="h-4 w-4 shrink-0 text-amber-700"
                    role="img"
                    :title="warningSummary(doc)"
                    :aria-label="warningSummary(doc)"
                  />
                </div>
                <p
                  v-if="doc.status === 'failed'"
                  class="mt-1 max-w-[16rem] truncate text-xs text-vermelho-700"
                  :title="apiError.errorText(doc.error_code, doc.error_params)"
                >
                  {{ apiError.errorText(doc.error_code, doc.error_params) }}
                </p>
              </td>
              <td v-if="cols.show('results')" class="px-4 py-3 text-right tabular-nums">{{ doc.result_count }}</td>
              <td v-if="cols.show('review')" class="px-4 py-3 text-right tabular-nums" :class="doc.review_count ? 'font-semibold text-amber-700' : ''">{{ doc.review_count }}</td>
              <td v-if="cols.show('outOfRange')" class="px-4 py-3 text-right tabular-nums" :class="doc.out_of_range_count ? 'font-semibold text-vermelho-600' : ''">{{ doc.out_of_range_count }}</td>
              <td v-if="cols.show('uploaded')" class="px-4 py-3 whitespace-nowrap text-sepia-500">{{ fmt.date(doc.created_at) }}</td>
              <td class="px-4 py-3 text-right">
                <button
                  type="button"
                  class="rounded p-2 text-sepia-500 hover:bg-vermelho-50 hover:text-vermelho-600 sm:p-1"
                  :aria-label="$t('common.delete')"
                  @click="toDelete = doc"
                >
                  <Icon icon="mdi:trash-can-outline" class="h-5 w-5" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="totalPages > 1" class="flex items-center justify-between border-t border-sepia-100 px-4 py-3 text-sm">
        <button type="button" class="rounded px-3 py-2 text-sepia-700 disabled:opacity-40 sm:py-1" :disabled="!hasPrevious" @click="previous">
          {{ $t('common.previous') }}
        </button>
        <span class="text-sepia-500">{{ $t('common.pageOf', { page, total: totalPages }) }}</span>
        <button type="button" class="rounded px-3 py-2 text-sepia-700 disabled:opacity-40 sm:py-1" :disabled="!hasNext" @click="next">
          {{ $t('common.next') }}
        </button>
      </div>
    </section>

    <Dialog v-model:visible="confirmingDelete" modal :header="$t('documents.deleteTitle')" class="w-[28rem] max-w-[95vw]">
      <p class="text-sm text-sepia-700">{{ $t('documents.deleteBody') }}</p>
      <div class="mt-6 flex justify-end gap-2">
        <Button
          type="button"
          class="!rounded-md !border !border-sepia-300 !bg-white !px-4 !py-2 !text-sm !text-sepia-800 hover:!bg-sepia-50"
          @click="toDelete = null"
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
