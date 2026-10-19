<script setup lang="ts">
import type { AnalyteOverview } from '@/types/exams'

definePageMeta({ middleware: 'auth' })
const { graphql } = useApi()
const fmt = useFormat()
const { t } = useI18n()
useHead({ title: () => t('results.title') })

const OVERVIEW_QUERY = `{
  analytesOverview {
    slug name category unit kind count latestValue latestText latestAt
    latestFlag latestInterpretation delta deltaPct sparkline
  }
}`

const { data: overview, error, refresh, status } = await useResilientAsyncData<AnalyteOverview[]>(
  'overview',
  async () =>
    (await graphql<{ data: { analytesOverview: AnalyteOverview[] } }>(OVERVIEW_QUERY))?.data
      ?.analytesOverview ?? [],
  { default: () => [] },
)

const cols = useColumnVisibility(
  'results',
  computed(() => [
    { key: 'exam', label: t('results.colExam'), required: true },
    { key: 'latest', label: t('results.colLatest'), required: true },
    { key: 'date', label: t('results.colDate'), mobileHidden: true },
    { key: 'situation', label: t('results.colSituation') },
    { key: 'change', label: t('results.colChange'), mobileHidden: true },
    { key: 'trend', label: t('results.colTrend'), mobileHidden: true },
    { key: 'count', label: t('results.colCount'), mobileHidden: true },
  ]),
)

const search = ref('')
const onlyOutOfRange = ref(false)

const CATEGORY_ORDER = [
  'hematology', 'glycemia', 'lipids', 'kidney', 'liver', 'electrolytes',
  'hormones', 'vitamins', 'iron', 'inflammation', 'tumor_markers', 'other',
]

// Busca sem acento e sem caixa: "glicose" encontra "Glicose" e "hemoglobina"
// encontra "Hemoglobina glicada"; "acido" encontra "Ácido úrico".
const fold = (text: string) => text.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

const filtered = computed(() => {
  const term = fold(search.value.trim())
  return overview.value.filter(
    (row) =>
      (!term || fold(row.name).includes(term)) &&
      (!onlyOutOfRange.value || ['low', 'high', 'abnormal'].includes(row.latestFlag)),
  )
})

const groups = computed(() =>
  CATEGORY_ORDER.map((category) => ({
    category,
    label: t(`category.${category}`),
    rows: filtered.value.filter((row) => row.category === category),
  })).filter((group) => group.rows.length),
)

const latestLabel = (row: AnalyteOverview) =>
  row.latestValue === null ? row.latestText || t('common.empty') : fmt.number(row.latestValue)
</script>

<template>
  <div class="space-y-6">
    <header>
      <h1 class="font-display text-2xl font-semibold text-sepia-900">{{ $t('results.title') }}</h1>
      <p class="mt-1 text-sm text-sepia-500">{{ $t('results.subtitle') }}</p>
    </header>

    <div class="flex flex-wrap items-center gap-4">
      <div class="relative w-full sm:w-auto">
        <Icon icon="mdi:magnify" class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-sepia-500" />
        <input
          v-model="search"
          type="search"
          :placeholder="$t('results.search')"
          :aria-label="$t('results.search')"
          class="h-11 w-full rounded-md border border-sepia-200 bg-white pl-9 pr-3 text-base focus:border-sepia-500 focus:outline-none sm:h-10 sm:w-72 sm:text-sm"
        >
      </div>
      <label class="flex items-center gap-2 text-sm text-sepia-700">
        <input v-model="onlyOutOfRange" type="checkbox" class="h-4 w-4 rounded border-sepia-300">
        {{ $t('results.onlyOutOfRange') }}
      </label>
      <ColumnToggle id="results" :cols="cols" class="sm:ml-auto" />
    </div>

    <AppLoadError v-if="error" :loading="status === 'pending'" @retry="refresh" />
    <p v-else-if="!overview.length" class="rounded-lg border border-sepia-200 bg-white p-10 text-center text-sepia-500">
      {{ $t('results.empty') }}
    </p>
    <p v-else-if="!groups.length" class="rounded-lg border border-sepia-200 bg-white p-10 text-center text-sepia-500">
      {{ $t('results.emptyFiltered') }}
    </p>

    <section
      v-for="group in groups"
      :key="group.category"
      class="overflow-hidden rounded-lg border border-sepia-200 bg-white shadow-sm"
    >
      <h2 class="border-b border-sepia-100 bg-sepia-50 px-4 py-2 text-sm font-semibold text-sepia-800">{{ group.label }}</h2>
      <div class="relative overflow-x-auto">
        <table class="min-w-full text-sm">
          <thead class="text-left text-xs uppercase tracking-wide text-sepia-500">
            <tr>
              <th class="px-4 py-2">{{ $t('results.colExam') }}</th>
              <th class="px-4 py-2 text-right">{{ $t('results.colLatest') }}</th>
              <th v-if="cols.show('date')" class="px-4 py-2">{{ $t('results.colDate') }}</th>
              <th v-if="cols.show('situation')" class="px-4 py-2">{{ $t('results.colSituation') }}</th>
              <th v-if="cols.show('change')" class="px-4 py-2 text-right">{{ $t('results.colChange') }}</th>
              <th v-if="cols.show('trend')" class="px-4 py-2">{{ $t('results.colTrend') }}</th>
              <th v-if="cols.show('count')" class="px-4 py-2 text-right">{{ $t('results.colCount') }}</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-sepia-100">
            <tr v-for="row in group.rows" :key="row.slug" class="hover:bg-sepia-50">
              <td class="px-4 py-3">
                <NuxtLink :to="`/analytes/${row.slug}`" class="font-medium text-sepia-800 underline decoration-sepia-300">
                  {{ row.name }}
                </NuxtLink>
              </td>
              <td class="px-4 py-3 text-right tabular-nums">
                <span class="font-semibold">{{ latestLabel(row) }}</span>
                <span class="ml-1 text-xs text-sepia-500">{{ row.unit }}</span>
              </td>
              <td v-if="cols.show('date')" class="px-4 py-3 whitespace-nowrap text-sepia-600">{{ fmt.date(row.latestAt) }}</td>
              <td v-if="cols.show('situation')" class="px-4 py-3">
                <ResultsFlagBadge :flag="row.latestFlag" />
                <p v-if="row.latestInterpretation" class="mt-0.5 text-xs text-sepia-500">{{ row.latestInterpretation }}</p>
              </td>
              <td v-if="cols.show('change')" class="px-4 py-3 text-right tabular-nums whitespace-nowrap">
                <template v-if="row.delta !== null">
                  {{ fmt.signedNumber(row.delta) }}
                  <span class="text-xs text-sepia-500">({{ fmt.signedPercent(row.deltaPct) }})</span>
                </template>
                <span v-else class="text-sepia-500">{{ $t('common.empty') }}</span>
              </td>
              <td v-if="cols.show('trend')" class="px-4 py-3"><ResultsSparkLine :values="row.sparkline" /></td>
              <td v-if="cols.show('count')" class="px-4 py-3 text-right tabular-nums">{{ row.count }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
