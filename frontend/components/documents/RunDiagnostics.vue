<script setup lang="ts">
import type { DiagnosticHint } from '@/composables/useRunDiagnostics'
import type { ExtractionRun } from '@/types/exams'

// "Diagnóstico da leitura": mostra por que uma leitura foi lenta sem precisar
// abrir os logs do Ollama. Tudo vem de `ExtractionRun.stats`, que pode estar
// incompleto em execuções antigas.
const props = defineProps<{ runs: ExtractionRun[], error?: boolean, loading?: boolean }>()
const emit = defineEmits<{ retry: [] }>()

const apiError = useApiError()
const fmt = useFormat()
const diag = useRunDiagnostics()

const latest = computed(() => props.runs[0] ?? null)
const older = computed(() => props.runs.slice(1))
const summary = computed(() => (latest.value ? summarizeRun(latest.value) : null))
const hints = computed(() => (summary.value ? summary.value.hints : []))

const hintTone = (hint: DiagnosticHint) =>
  hint === 'noModel' ? 'border-azul-200 bg-azul-50 text-azul-900' : 'border-amber-200 bg-amber-50 text-amber-900'
const hintIcon = (hint: DiagnosticHint) => (hint === 'noModel' ? 'mdi:information-outline' : 'mdi:alert-outline')
</script>

<template>
  <details v-if="latest || error" class="rounded-lg border border-sepia-200 bg-white shadow-sm">
    <summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-sepia-800">
      {{ $t('document.diagnosticsTitle') }}
    </summary>

    <div class="space-y-4 px-4 pb-4">
      <AppLoadError v-if="error" :loading="loading" @retry="emit('retry')" />

      <template v-if="latest && summary">
        <p
          v-if="latest.status === 'failed'"
          class="flex items-start gap-2 rounded-md border border-vermelho-200 bg-vermelho-50 px-3 py-2 text-sm text-vermelho-800"
        >
          <Icon icon="mdi:alert-circle-outline" class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          {{ apiError.errorText(latest.error_code, latest.error_params) }}
        </p>

        <dl class="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div>
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagStatus') }}</dt>
            <dd class="text-sm font-medium">{{ $t(`runStatus.${latest.status}`) }}</dd>
          </div>
          <div v-if="latest.model">
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagModel') }}</dt>
            <dd class="break-all text-sm font-medium">{{ latest.model }}</dd>
          </div>
          <div v-if="latest.vision_model">
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagVisionModel') }}</dt>
            <dd class="break-all text-sm font-medium">{{ latest.vision_model }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagTotalDuration') }}</dt>
            <dd class="text-sm font-medium tabular-nums">
              {{ summary.running ? $t('document.diagRunning') : diag.duration(summary.durationSeconds) }}
            </dd>
          </div>
          <div v-if="summary.ruleBlocks !== null">
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagRuleBlocks') }}</dt>
            <dd class="text-sm font-medium tabular-nums">
              <template v-if="summary.totalBlocks !== null">
                {{ $t('document.diagOfBlocks', { count: diag.count(summary.ruleBlocks), total: diag.count(summary.totalBlocks) }) }}
              </template>
              <template v-else>{{ diag.count(summary.ruleBlocks) }}</template>
            </dd>
          </div>
          <div v-if="summary.llmCalls !== null">
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagModelCalls') }}</dt>
            <dd class="text-sm font-medium tabular-nums">{{ diag.count(summary.llmCalls) }}</dd>
          </div>
          <div v-if="summary.llmSeconds !== null">
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagModelTime') }}</dt>
            <dd class="text-sm font-medium tabular-nums">{{ diag.duration(summary.llmSeconds) }}</dd>
          </div>
          <div v-if="summary.avgTokensPerSecond !== null">
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagAvgSpeed') }}</dt>
            <dd class="text-sm font-medium tabular-nums">{{ diag.speedWithUnit(summary.avgTokensPerSecond) }}</dd>
          </div>
          <div v-if="summary.loadSeconds !== null">
            <dt class="text-xs uppercase tracking-wide text-sepia-500">{{ $t('document.diagLoadTime') }}</dt>
            <dd class="text-sm font-medium tabular-nums">{{ diag.duration(summary.loadSeconds) }}</dd>
          </div>
        </dl>

        <p v-if="!summary.hasStats && !summary.running" class="text-xs text-sepia-500">
          {{ $t('document.diagNoStats') }}
        </p>

        <div v-if="hints.length" class="space-y-2">
          <p
            v-for="hint in hints"
            :key="hint"
            role="note"
            :class="['flex items-start gap-2 rounded-md border px-3 py-2 text-sm', hintTone(hint)]"
          >
            <Icon :icon="hintIcon(hint)" class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {{ diag.hintText(hint, summary) }}
          </p>
        </div>

        <div v-if="summary.calls.length" class="overflow-x-auto rounded-md border border-sepia-100">
          <table class="min-w-full text-sm">
            <caption class="sr-only">{{ $t('document.diagCallsCaption') }}</caption>
            <thead class="bg-sepia-50 text-left text-xs uppercase tracking-wide text-sepia-500">
              <tr>
                <th scope="col" class="px-3 py-2">{{ $t('document.diagColKind') }}</th>
                <th scope="col" class="px-3 py-2 text-right">{{ $t('document.diagColPage') }}</th>
                <th scope="col" class="px-3 py-2 text-right">{{ $t('document.diagColDuration') }}</th>
                <th scope="col" class="px-3 py-2 text-right">{{ $t('document.diagColLoad') }}</th>
                <th scope="col" class="px-3 py-2 text-right">{{ $t('document.diagColTokens') }}</th>
                <th scope="col" class="px-3 py-2 text-right">{{ $t('document.diagColSpeed') }}</th>
                <th scope="col" class="px-3 py-2">{{ $t('document.diagColOutcome') }}</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-sepia-100">
              <tr v-for="(call, index) in summary.calls" :key="index">
                <td class="px-3 py-2">{{ diag.kindLabel(call.kind) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ diag.count(call.page) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ diag.seconds(call.seconds) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ diag.seconds(call.load_seconds) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ diag.count(call.output_tokens) }}</td>
                <td class="px-3 py-2 text-right tabular-nums">{{ diag.speed(call.tokens_per_second) }}</td>
                <td class="px-3 py-2">
                  <span :class="['inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium', OUTCOME_STYLES[call.outcome] ?? 'text-sepia-700']">
                    <Icon :icon="OUTCOME_ICONS[call.outcome] ?? 'mdi:help-circle-outline'" class="h-4 w-4" aria-hidden="true" />
                    {{ diag.outcomeLabel(call.outcome) }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <section v-if="older.length">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-sepia-500">{{ $t('document.diagPreviousRuns') }}</h3>
        <ul class="mt-1 divide-y divide-sepia-100 text-sm">
          <li v-for="item in older" :key="item.id" class="flex flex-wrap items-center justify-between gap-2 py-1.5">
            <span class="tabular-nums text-sepia-700">{{ fmt.dateTime(item.started_at) }}</span>
            <span class="text-sepia-600">{{ $t(`runStatus.${item.status}`) }}</span>
            <span class="tabular-nums text-sepia-600">
              {{ isRunInProgress(item) ? $t('document.diagRunning') : diag.duration(runDurationSeconds(item)) }}
            </span>
          </li>
        </ul>
      </section>
    </div>
  </details>
</template>
