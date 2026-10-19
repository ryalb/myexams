<script setup lang="ts">
import type { AnalyteHistory, HistoryPoint } from '@/types/exams'

definePageMeta({ middleware: 'auth' })
const route = useRoute()
const slug = String(route.params.slug)
const { graphql } = useApi()
const fmt = useFormat()
const { t } = useI18n()
const cols = useColumnVisibility(
  'analyte-history',
  computed(() => [
    { key: 'date', label: t('analyte.colDate'), required: true },
    { key: 'laboratory', label: t('analyte.colLaboratory'), mobileHidden: true },
    { key: 'printed', label: t('analyte.colPrinted'), required: true },
    { key: 'unit', label: t('analyte.colUnit') },
    { key: 'canonical', label: t('analyte.colCanonical'), mobileHidden: true },
    { key: 'reference', label: t('analyte.colReference'), mobileHidden: true },
    { key: 'situation', label: t('analyte.colSituation') },
    { key: 'origin', label: t('analyte.colOrigin'), mobileHidden: true },
  ]),
)

const HISTORY_QUERY = `query ($slug: String!) {
  analyteHistory(slug: $slug) {
    chartable
    analyte { id slug name category unit kind }
    points {
      id documentId laboratory origin collectedAt value valueRaw valueText comparator unit
      refLow refHigh referenceRaw flag interpretation reviewStatus
    }
  }
}`

// `null` = o exame não existe (ou não é seu); falha de rede/servidor vira `error`
// e mantém o último histórico carregado.
const { data: history, error, refresh, status } = await useResilientAsyncData<AnalyteHistory | null>(
  `analyte-${slug}`,
  async () =>
    (await graphql<{ data: { analyteHistory: AnalyteHistory | null } }>(HISTORY_QUERY, { slug }))
      ?.data?.analyteHistory ?? null,
  { default: () => null },
)

useHead({ title: () => history.value?.analyte.name ?? t('nav.results') })

type Range = 'all' | '1' | '3' | '5'
const range = ref<Range>('all')
const RANGES: { value: Range; labelKey: string }[] = [
  { value: 'all', labelKey: 'analyte.rangeAll' },
  { value: '1', labelKey: 'analyte.rangeOneYear' },
  { value: '3', labelKey: 'analyte.rangeThreeYears' },
  { value: '5', labelKey: 'analyte.rangeFiveYears' },
]

const points = computed<HistoryPoint[]>(() => {
  const all = history.value?.points ?? []
  if (range.value === 'all') return all
  const since = new Date()
  since.setFullYear(since.getFullYear() - Number(range.value))
  return all.filter((p) => p.collectedAt && new Date(p.collectedAt) >= since)
})

const latest = computed(() => (history.value?.points ?? []).at(-1) ?? null)
// Tabela: mais recente primeiro.
const tableRows = computed(() => [...points.value].reverse())
</script>

<template>
  <div class="space-y-6">
    <NuxtLink to="/results" class="inline-flex items-center gap-1 text-sm text-sepia-600 hover:text-sepia-900">
      <Icon icon="mdi:arrow-left" class="h-4 w-4" />
      {{ $t('analyte.backToResults') }}
    </NuxtLink>

    <AppLoadError v-if="error" :loading="status === 'pending'" @retry="refresh" />

    <p v-if="!history && !error" class="rounded-lg border border-sepia-200 bg-white p-10 text-center text-sepia-500">
      {{ $t('analyte.notFound') }}
    </p>

    <template v-else-if="history">
      <header class="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p class="text-xs font-medium uppercase tracking-widest text-sepia-500">{{ $t(`category.${history.analyte.category}`) }}</p>
          <h1 class="font-display text-2xl font-semibold text-sepia-900">{{ history.analyte.name }}</h1>
          <p class="mt-1 text-sm text-sepia-500">{{ $t('analyte.measurements', { count: history.points.length }, history.points.length) }}</p>
        </div>
        <div v-if="latest" class="text-right">
          <p class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('analyte.latest') }}</p>
          <p class="text-2xl font-semibold tabular-nums">
            {{ latest.value === null ? latest.valueRaw : fmt.number(latest.value) }}
            <span class="text-sm font-normal text-sepia-500">{{ history.analyte.unit }}</span>
          </p>
          <ResultsFlagBadge :flag="latest.flag" />
        </div>
      </header>

      <p v-if="!history.points.length" class="rounded-lg border border-sepia-200 bg-white p-10 text-center text-sepia-500">
        {{ $t('analyte.noPoints') }}
      </p>

      <template v-else>
        <section class="rounded-lg border border-sepia-200 bg-white p-4 shadow-sm">
          <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
            <h2 class="text-sm font-semibold text-sepia-800">{{ $t('analyte.chartTitle') }}</h2>
            <div v-if="history.chartable" class="inline-flex overflow-hidden rounded-md border border-sepia-200" role="group">
              <button
                v-for="option in RANGES"
                :key="option.value"
                type="button"
                :aria-pressed="range === option.value"
                :class="[
                  'px-3 py-2.5 text-xs sm:py-1.5',
                  range === option.value ? 'bg-sepia-900 text-sepia-50' : 'bg-white text-sepia-700 hover:bg-sepia-50',
                ]"
                @click="range = option.value"
              >
                {{ $t(option.labelKey) }}
              </button>
            </div>
          </div>

          <template v-if="history.chartable">
            <ResultsAnalyteChart :points="points" :unit="history.analyte.unit" />
            <p class="mt-2 flex items-center gap-2 text-xs text-sepia-500">
              <span class="inline-block h-3 w-5 rounded-sm bg-azul-200/60" />
              {{ $t('analyte.referenceBand') }}
            </p>
          </template>
          <p v-else class="py-6 text-center text-sm text-sepia-500">{{ $t('analyte.notChartable') }}</p>
        </section>

        <section class="overflow-hidden rounded-lg border border-sepia-200 bg-white shadow-sm">
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-sepia-100 bg-sepia-50 px-4 py-2">
            <h2 class="text-sm font-semibold text-sepia-800">{{ $t('analyte.tableTitle') }}</h2>
            <ColumnToggle id="analyte-history" :cols="cols" />
          </div>
          <div class="overflow-x-auto">
            <table class="min-w-full text-sm">
              <thead class="text-left text-xs uppercase tracking-wide text-sepia-500">
                <tr>
                  <th class="px-4 py-2">{{ $t('analyte.colDate') }}</th>
                  <th v-if="cols.show('laboratory')" class="px-4 py-2">{{ $t('analyte.colLaboratory') }}</th>
                  <th class="px-4 py-2 text-right">{{ $t('analyte.colPrinted') }}</th>
                  <th v-if="cols.show('unit')" class="px-4 py-2">{{ $t('analyte.colUnit') }}</th>
                  <th v-if="cols.show('canonical')" class="px-4 py-2 text-right">{{ $t('analyte.colCanonical') }}</th>
                  <th v-if="cols.show('reference')" class="px-4 py-2">{{ $t('analyte.colReference') }}</th>
                  <th v-if="cols.show('situation')" class="px-4 py-2">{{ $t('analyte.colSituation') }}</th>
                  <th v-if="cols.show('origin')" class="px-4 py-2">{{ $t('analyte.colOrigin') }}</th>
                  <th class="px-4 py-2"><span class="sr-only">{{ $t('analyte.openDocument') }}</span></th>
                </tr>
              </thead>
              <tbody class="divide-y divide-sepia-100">
                <tr v-for="point in tableRows" :key="point.id" class="align-top hover:bg-sepia-50">
                  <td class="px-4 py-3 whitespace-nowrap">{{ fmt.date(point.collectedAt) }}</td>
                  <td v-if="cols.show('laboratory')" class="px-4 py-3">{{ point.laboratory || $t('common.empty') }}</td>
                  <td class="px-4 py-3 text-right tabular-nums font-medium">{{ point.valueRaw || $t('common.empty') }}</td>
                  <td v-if="cols.show('unit')" class="px-4 py-3 text-sepia-600">{{ point.unit || $t('common.empty') }}</td>
                  <td v-if="cols.show('canonical')" class="px-4 py-3 text-right tabular-nums">{{ fmt.number(point.value) }}</td>
                  <td v-if="cols.show('reference')" class="px-4 py-3 max-w-[18rem] whitespace-pre-line text-xs text-sepia-600">{{ point.referenceRaw || $t('common.empty') }}</td>
                  <td v-if="cols.show('situation')" class="px-4 py-3">
                    <ResultsFlagBadge :flag="point.flag" />
                    <p v-if="point.interpretation" class="mt-0.5 text-xs text-sepia-500">{{ point.interpretation }}</p>
                  </td>
                  <td v-if="cols.show('origin')" class="px-4 py-3 text-xs text-sepia-500">{{ $t(`origin.${point.origin}`) }}</td>
                  <td class="px-4 py-3 text-right">
                    <NuxtLink
                      :to="`/documents/${point.documentId}`"
                      class="text-sepia-500 hover:text-sepia-900"
                      :aria-label="$t('analyte.openDocument')"
                    >
                      <Icon icon="mdi:open-in-new" class="h-4 w-4" />
                    </NuxtLink>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>
