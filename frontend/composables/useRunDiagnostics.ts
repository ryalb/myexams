import type { ExtractionRun, LlmCallDetail } from '@/types/exams'

// Diagnóstico de uma execução da leitura: a derivação (funções puras, sem efeito
// colateral) fica aqui; a formatação em pt-BR, em `useRunDiagnostics`.

const SLOW_TOKENS_PER_SECOND = 5
const COLD_LOAD_SECONDS = 30

// 'noModel' é informativo; os demais indicam lentidão ou falha.
export type DiagnosticHint = 'slow' | 'timeout' | 'coldLoad' | 'noModel'

export interface RunSummary {
  running: boolean
  failed: boolean
  /** Duração total em segundos; `null` enquanto roda ou se faltar um dos carimbos. */
  durationSeconds: number | null
  /** Blocos lidos por regras; `null` se a execução não guardou o dado. */
  ruleBlocks: number | null
  totalBlocks: number | null
  llmCalls: number | null
  llmSeconds: number | null
  avgTokensPerSecond: number | null
  loadSeconds: number | null
  calls: LlmCallDetail[]
  /** Há alguma métrica guardada (execuções antigas ou recém-criadas podem não ter). */
  hasStats: boolean
  hints: DiagnosticHint[]
}

const finiteOrNull = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

export function runDurationSeconds(run: Pick<ExtractionRun, 'started_at' | 'finished_at'>): number | null {
  if (!run.started_at || !run.finished_at) return null
  const start = Date.parse(run.started_at)
  const end = Date.parse(run.finished_at)
  if (Number.isNaN(start) || Number.isNaN(end)) return null
  return Math.max(0, (end - start) / 1000)
}

export const isRunInProgress = (run: Pick<ExtractionRun, 'status'>): boolean =>
  run.status === 'queued' || run.status === 'running'

export function summarizeRun(run: ExtractionRun): RunSummary {
  const stats = run.stats ?? {}
  const running = isRunInProgress(run)
  const calls = Array.isArray(stats.llm_calls_detail) ? stats.llm_calls_detail : []
  const llmCalls = finiteOrNull(stats.llm_calls)
  const avgTokensPerSecond = finiteOrNull(stats.llm_avg_tokens_per_second)
  const loadSeconds = finiteOrNull(stats.llm_load_seconds)

  const hints: DiagnosticHint[] = []
  if (avgTokensPerSecond !== null && avgTokensPerSecond < SLOW_TOKENS_PER_SECOND) hints.push('slow')
  if (calls.some((call) => call.outcome === 'timeout')) hints.push('timeout')
  if (loadSeconds !== null && loadSeconds >= COLD_LOAD_SECONDS) hints.push('coldLoad')
  if (llmCalls === 0 && run.status === 'success') hints.push('noModel')

  return {
    running,
    failed: run.status === 'failed',
    durationSeconds: running ? null : runDurationSeconds(run),
    ruleBlocks: finiteOrNull(stats.rule_blocks),
    totalBlocks: finiteOrNull(stats.blocks),
    llmCalls,
    llmSeconds: finiteOrNull(stats.llm_seconds),
    avgTokensPerSecond,
    loadSeconds: loadSeconds !== null && loadSeconds > 0 ? loadSeconds : null,
    calls,
    hasStats: Object.keys(stats).length > 0,
    hints,
  }
}

export const OUTCOME_ICONS: Record<string, string> = {
  ok: 'mdi:check-circle-outline',
  timeout: 'mdi:clock-alert-outline',
  error: 'mdi:close-circle-outline',
}

export const OUTCOME_STYLES: Record<string, string> = {
  ok: 'text-azul-700',
  timeout: 'text-amber-800',
  error: 'text-vermelho-700',
}

export function useRunDiagnostics() {
  const { t, te, n } = useI18n()
  const empty = () => t('common.empty')

  const seconds = (value: number | null | undefined) =>
    value === null || value === undefined ? empty() : n(value, 'fixed1')
  const count = (value: number | null | undefined) =>
    value === null || value === undefined ? empty() : n(value, 'integer')
  const speed = (value: number | null | undefined) =>
    value === null || value === undefined ? empty() : n(value, 'fixed1')

  // "12,3 s" abaixo de um minuto; "2 min 5 s" acima.
  const duration = (value: number | null | undefined) => {
    if (value === null || value === undefined) return empty()
    if (value < 60) return t('document.diagSecondsUnit', { value: n(value, 'fixed1') })
    const whole = Math.round(value)
    return t('document.diagMinutesUnit', {
      minutes: n(Math.floor(whole / 60), 'integer'),
      seconds: n(whole % 60, 'integer'),
    })
  }
  const speedWithUnit = (value: number | null | undefined) =>
    value === null || value === undefined ? empty() : t('document.diagSpeedUnit', { value: n(value, 'fixed1') })

  const kindLabel = (kind: string) => (te(`document.diagKind.${kind}`) ? t(`document.diagKind.${kind}`) : kind)
  const outcomeLabel = (outcome: string) =>
    te(`document.diagOutcome.${outcome}`) ? t(`document.diagOutcome.${outcome}`) : outcome

  const hintText = (hint: DiagnosticHint, summary: RunSummary) => {
    switch (hint) {
      case 'slow':
        return t('document.diagSlowHint')
      case 'timeout':
        return t('document.diagTimeoutHint')
      case 'coldLoad':
        return t('document.diagColdLoadHint', { seconds: n(Math.round(summary.loadSeconds ?? 0), 'integer') })
      case 'noModel':
        return t('document.diagNoModelHint')
    }
  }

  return { seconds, count, speed, duration, speedWithUnit, kindLabel, outcomeLabel, hintText }
}
