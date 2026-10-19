<script setup lang="ts">
import Button from 'primevue/button'
import {
  buildReportMarkdown,
  reportFilename,
  type ReportData,
  type ReportRange,
} from '@/utils/report'

definePageMeta({ middleware: 'auth' })
const { get } = useApi()
const { t } = useI18n()
const notice = useNotice()
useHead({ title: () => t('report.title') })

const { data, error, refresh, status } = await useResilientAsyncData<ReportData | null>(
  'report',
  async () => await get<ReportData>('/report/'),
  { default: () => null },
)

const range = ref<ReportRange>('all')
const includePrompt = ref(true)
const rangeOptions = computed(() => [
  { value: 'all', label: t('analyte.rangeAll') },
  { value: '1y', label: t('analyte.rangeOneYear') },
  { value: '3y', label: t('analyte.rangeThreeYears') },
  { value: '5y', label: t('analyte.rangeFiveYears') },
])

const markdown = computed(() =>
  data.value
    ? buildReportMarkdown(
        data.value,
        { category: (code) => t(`category.${code}`), sex: (code) => t(`sex.${code}`) },
        { range: range.value, includePrompt: includePrompt.value },
      )
    : '',
)
const hasResults = computed(() => (data.value?.analytes.length ?? 0) > 0)
// Estimativa grosseira para português (~3,5 caracteres por token); só dá a ordem de grandeza.
const size = computed(() => ({
  chars: markdown.value.length,
  tokens: Math.ceil(markdown.value.length / 3.5),
}))

const box = ref<HTMLTextAreaElement | null>(null)

async function copy() {
  try {
    await navigator.clipboard.writeText(markdown.value)
    notice.success(t('report.copied'))
  } catch {
    // Sem permissão ou fora de contexto seguro: deixa o texto selecionado para Ctrl+C.
    box.value?.focus()
    box.value?.select()
    notice.error(t('report.copyFailed'))
  }
}

function download() {
  const url = URL.createObjectURL(new Blob([markdown.value], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = reportFilename()
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="space-y-6">
    <header>
      <h1 class="font-display text-2xl font-semibold text-sepia-900">{{ $t('report.title') }}</h1>
      <p class="mt-1 text-sm text-sepia-500">{{ $t('report.subtitle') }}</p>
    </header>

    <AppLoadError v-if="error" :loading="status === 'pending'" @retry="refresh" />

    <p v-else-if="!data" class="text-sm text-sepia-500">{{ $t('common.loading') }}</p>

    <p v-else-if="!hasResults" class="rounded-lg border border-sepia-200 bg-white px-4 py-8 text-center text-sm text-sepia-500">
      {{ $t('results.empty') }}
    </p>

    <template v-else>
      <div
        class="flex items-start gap-3 rounded-lg border border-sepia-200 bg-white px-4 py-3 text-sm text-sepia-700"
      >
        <Icon icon="mdi:shield-lock-outline" class="mt-0.5 h-5 w-5 shrink-0 text-sepia-500" aria-hidden="true" />
        <p>{{ $t('report.privacyNote') }}</p>
      </div>

      <div class="flex flex-wrap items-center gap-4">
        <label class="flex items-center gap-2 text-sm text-sepia-700">
          {{ $t('report.period') }}
          <select
            v-model="range"
            class="h-11 rounded-md border border-sepia-200 bg-white px-3 text-base focus:border-sepia-500 focus:outline-none sm:h-10 sm:text-sm"
          >
            <option v-for="option in rangeOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="flex items-center gap-2 text-sm text-sepia-700">
          <input v-model="includePrompt" type="checkbox" class="h-4 w-4 rounded border-sepia-300">
          {{ $t('report.includePrompt') }}
        </label>
        <div class="ml-auto flex gap-2">
          <Button
            type="button"
            class="!rounded-md !bg-sepia-900 !px-4 !py-2 !text-sm !font-medium !text-sepia-50 hover:!bg-sepia-950"
            @click="copy"
          >
            <Icon icon="mdi:content-copy" class="mr-2 h-4 w-4" aria-hidden="true" />
            {{ $t('report.copy') }}
          </Button>
          <Button
            type="button"
            class="!rounded-md !border !border-sepia-300 !bg-white !px-4 !py-2 !text-sm !font-medium !text-sepia-800 hover:!bg-sepia-50"
            @click="download"
          >
            <Icon icon="mdi:download" class="mr-2 h-4 w-4" aria-hidden="true" />
            {{ $t('report.download') }}
          </Button>
        </div>
      </div>

      <div>
        <textarea
          ref="box"
          :value="markdown"
          readonly
          rows="26"
          spellcheck="false"
          :aria-label="$t('report.previewLabel')"
          class="w-full rounded-lg border border-sepia-200 bg-white p-4 font-mono text-xs leading-relaxed text-sepia-900 focus:border-sepia-500 focus:outline-none"
        />
        <p class="mt-1 text-xs text-sepia-500">
          {{ $t('report.size', { chars: size.chars, tokens: size.tokens }) }}
        </p>
      </div>
    </template>
  </div>
</template>
