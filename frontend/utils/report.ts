// Relatório executivo em Markdown, pensado para ser colado em um modelo de linguagem remoto.
// Função pura (sem auto-imports do Nuxt): os rótulos traduzidos entram por parâmetro.
//
// Privacidade: o texto sai do app. Por isso só entram sexo, idade, exames, valores e datas;
// nunca nome, e-mail, laboratório, número do pedido ou nome de arquivo.

export interface ReportPoint {
  collected_at: string | null
  origin: string
  value: number | null
  value_raw: string
  value_text: string
  comparator: string
  unit: string
  ref_low: number | null
  ref_high: number | null
  reference_raw: string
  flag: string
  review_status: string
}

export interface ReportAnalyte {
  slug: string
  name: string
  category: string
  unit: string
  kind: string
  points: ReportPoint[]
}

export interface ReportData {
  generated_at: string
  profile: { sex: string; age: number | null }
  analytes: ReportAnalyte[]
}

export type ReportRange = 'all' | '1y' | '3y' | '5y'

export interface ReportOptions {
  range: ReportRange
  includePrompt: boolean
  // Só para teste: data de referência do filtro de período.
  now?: Date
}

export interface ReportLabels {
  category: (code: string) => string
  sex: (code: string) => string
}

export const CATEGORY_ORDER = [
  'hematology', 'glycemia', 'lipids', 'kidney', 'liver', 'electrolytes',
  'hormones', 'vitamins', 'iron', 'inflammation', 'tumor_markers', 'other',
]

const RANGE_YEARS: Record<ReportRange, number | null> = { all: null, '1y': 1, '3y': 3, '5y': 5 }
const RANGE_TEXT: Record<ReportRange, string> = {
  all: 'todo o histórico',
  '1y': 'último ano',
  '3y': 'últimos 3 anos',
  '5y': 'últimos 5 anos',
}
// Texto curto e inequívoco para o modelo; `normal` e `unknown` ficam sem marca nas séries.
const FLAG_TEXT: Record<string, string> = {
  low: 'BAIXO',
  high: 'ALTO',
  abnormal: 'ALTERADO',
  normal: 'normal',
  unknown: 'sem referência',
}
const OUT_OF_RANGE = new Set(['low', 'high', 'abnormal'])
const COMPARATOR: Record<string, string> = { lt: '<', lte: '≤', gt: '>', gte: '≥' }

const MAX_PRIOR = 20
const BIG_CHANGE_PCT = 20
const BIG_CHANGE_ROWS = 10
const REFERENCE_TEXT_MAX = 100

const PROMPT = `## Instruções para a análise

Você é um assistente que ajuda a interpretar exames laboratoriais. O relatório abaixo é de uma
única pessoa e traz o último resultado de cada exame e o histórico anterior (mais recente primeiro).
Com base nele:

1. Resuma o quadro geral em poucas linhas.
2. Destaque os resultados fora da referência e diga se persistem, pioram ou melhoram ao longo do tempo.
3. Aponte tendências relevantes mesmo quando o último valor está dentro da referência.
4. Agrupe achados que se relacionam (por exemplo, perfil lipídico, glicemia, função renal).
5. Liste perguntas objetivas para levar ao médico e exames de acompanhamento que possam fazer sentido.

Isto não substitui avaliação médica: não dê diagnóstico nem sugira mudar medicamentos.
Se faltar informação (idade, sexo, jejum, uso de medicamentos), diga o que seria necessário.
`

function isoDay(value: string | null): string | null {
  if (!value) return null
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return null
  // Os laudos são de São Paulo: o dia civil é o de lá, não o do UTC do banco.
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(parsed)
}

function dayLabel(point: ReportPoint): string {
  return isoDay(point.collected_at) ?? 'sem data'
}

function number(value: number): string {
  return String(Number(value.toFixed(4)))
}

function cell(text: string): string {
  return text.replace(/\s+/g, ' ').replace(/\|/g, '/').trim() || '—'
}

function valueText(point: ReportPoint): string {
  if (point.value !== null) {
    return `${COMPARATOR[point.comparator] ?? ''}${number(point.value)}`
  }
  return cell(point.value_text || point.value_raw)
}

function referenceText(point: ReportPoint, unit: string): string {
  const { ref_low: low, ref_high: high } = point
  const suffix = unit ? ` ${unit}` : ''
  if (low !== null && high !== null) return `${number(low)} a ${number(high)}${suffix}`
  if (high !== null) return `até ${number(high)}${suffix}`
  if (low !== null) return `a partir de ${number(low)}${suffix}`
  const printed = cell(point.reference_raw)
  return printed.length > REFERENCE_TEXT_MAX ? `${printed.slice(0, REFERENCE_TEXT_MAX)}…` : printed
}

function flagText(flag: string): string {
  return FLAG_TEXT[flag] ?? FLAG_TEXT.unknown
}

function seriesText(point: ReportPoint): string {
  const mark = OUT_OF_RANGE.has(point.flag) ? ` (${flagText(point.flag)})` : ''
  const unconfirmed = point.review_status === 'needs_review' ? ' (?)' : ''
  return `${dayLabel(point)}: ${valueText(point)}${mark}${unconfirmed}`
}

function periodStart(range: ReportRange, now: Date): string | null {
  const years = RANGE_YEARS[range]
  if (years === null) return null
  const start = new Date(now)
  start.setFullYear(start.getFullYear() - years)
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(start)
}

// "Anteriores impressos" repetem valores já presentes em laudos antigos: mesmo dia e mesmo
// valor contam uma vez só, e fica a linha do próprio laudo.
function dedupe(points: ReportPoint[]): ReportPoint[] {
  const ordered = [...points].sort((a, b) => Number(a.origin === 'lab_previous') - Number(b.origin === 'lab_previous'))
  const seen = new Set<string>()
  const kept: ReportPoint[] = []
  for (const point of ordered) {
    const key = `${dayLabel(point)}|${point.value ?? (point.value_text || point.value_raw)}`
    if (seen.has(key)) continue
    seen.add(key)
    kept.push(point)
  }
  return kept
}

function chronological(points: ReportPoint[]): ReportPoint[] {
  return [...points].sort((a, b) => {
    const left = isoDay(a.collected_at)
    const right = isoDay(b.collected_at)
    if (left === right) return 0
    if (left === null) return -1
    if (right === null) return 1
    return left < right ? -1 : 1
  })
}

interface Prepared {
  analyte: ReportAnalyte
  points: ReportPoint[]
  latest: ReportPoint
  previous: ReportPoint | null
  changePct: number | null
}

function prepare(analytes: ReportAnalyte[], range: ReportRange, now: Date): Prepared[] {
  const start = periodStart(range, now)
  const prepared: Prepared[] = []
  for (const analyte of analytes) {
    let points = chronological(dedupe(analyte.points))
    if (start) points = points.filter((p) => (isoDay(p.collected_at) ?? '') >= start)
    const latest = points.at(-1)
    if (!latest) continue
    const numeric = points.filter((p) => p.value !== null)
    const last = numeric.at(-1)
    const before = numeric.at(-2) ?? null
    const changePct =
      last && before && last === latest && before.value
        ? (((last.value as number) - before.value) / Math.abs(before.value)) * 100
        : null
    prepared.push({ analyte, points, latest, previous: before, changePct })
  }
  return prepared
}

function signedPercent(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
}

function table(header: string[], rows: string[][]): string[] {
  return [
    `| ${header.join(' | ')} |`,
    `|${header.map(() => '---').join('|')}|`,
    ...rows.map((row) => `| ${row.join(' | ')} |`),
  ]
}

export function buildReportMarkdown(
  data: ReportData,
  labels: ReportLabels,
  options: ReportOptions,
): string {
  const now = options.now ?? new Date()
  const prepared = prepare(data.analytes, options.range, now)
  const lines: string[] = ['# Relatório de exames laboratoriais', '']

  const profile: string[] = []
  if (data.profile.sex) profile.push(`sexo ${labels.sex(data.profile.sex).toLowerCase()}`)
  if (data.profile.age !== null) profile.push(`${data.profile.age} anos`)

  const days = prepared.flatMap((p) => p.points.map(dayLabel)).filter((d) => d !== 'sem data').sort()
  const span = days.length ? ` (${days[0]} a ${days[days.length - 1]})` : ''
  const total = prepared.reduce((sum, p) => sum + p.points.length, 0)
  lines.push(
    `- Gerado em: ${isoDay(data.generated_at) ?? isoDay(now.toISOString())}`,
    `- Paciente: ${profile.length ? profile.join(', ') : 'não informado'}`,
    `- Período: ${RANGE_TEXT[options.range]}${span}`,
    `- Conteúdo: ${prepared.length} exames, ${total} resultados`,
    '',
  )

  if (!prepared.length) {
    lines.push('Nenhum resultado no período.', '')
    return `${lines.join('\n').trimEnd()}\n`
  }

  if (options.includePrompt) lines.push(PROMPT)

  // --- Resumo -----------------------------------------------------------------------------
  const outside = prepared.filter((p) => OUT_OF_RANGE.has(p.latest.flag))
  const noReference = prepared.filter((p) => p.latest.flag === 'unknown').length
  lines.push(
    '## Resumo',
    '',
    `- Exames com o último resultado fora da referência: ${outside.length} de ${prepared.length}`,
    `- Exames sem referência para comparar: ${noReference}`,
    '',
  )

  if (outside.length) {
    lines.push('### Fora da referência no último resultado', '')
    lines.push(
      ...table(
        ['Exame', 'Data', 'Último', 'Referência', 'Situação', 'Variação vs. anterior'],
        outside.map((p) => [
          cell(p.analyte.name),
          dayLabel(p.latest),
          `${valueText(p.latest)}${p.latest.unit ? ` ${p.latest.unit}` : ''}`,
          referenceText(p.latest, p.latest.unit),
          flagText(p.latest.flag),
          p.changePct === null ? '—' : signedPercent(p.changePct),
        ]),
      ),
      '',
    )
  }

  const changes = prepared
    .filter((p) => p.changePct !== null && Math.abs(p.changePct) >= BIG_CHANGE_PCT)
    .sort((a, b) => Math.abs(b.changePct as number) - Math.abs(a.changePct as number))
    .slice(0, BIG_CHANGE_ROWS)
  if (changes.length) {
    lines.push(`### Maiores variações (≥ ${BIG_CHANGE_PCT}% vs. resultado anterior)`, '')
    lines.push(
      ...table(
        ['Exame', 'Anterior', 'Último', 'Variação', 'Situação'],
        changes.map((p) => [
          cell(p.analyte.name),
          `${dayLabel(p.previous as ReportPoint)}: ${valueText(p.previous as ReportPoint)}`,
          `${dayLabel(p.latest)}: ${valueText(p.latest)}`,
          signedPercent(p.changePct as number),
          flagText(p.latest.flag),
        ]),
      ),
      '',
    )
  }

  // --- Detalhe por categoria --------------------------------------------------------------
  lines.push('## Resultados por categoria', '')
  const categories = [...CATEGORY_ORDER, ...new Set(prepared.map((p) => p.analyte.category))]
  for (const category of [...new Set(categories)]) {
    const rows = prepared.filter((p) => p.analyte.category === category)
    if (!rows.length) continue
    lines.push(`### ${labels.category(category)}`, '')
    lines.push(
      ...table(
        ['Exame', 'Unidade', 'Referência', 'Último', 'Situação', 'Anteriores (mais recente primeiro)'],
        rows.map((p) => {
          const prior = p.points.slice(0, -1).reverse()
          const shown = prior.slice(0, MAX_PRIOR).map(seriesText)
          if (prior.length > MAX_PRIOR) shown.push(`+${prior.length - MAX_PRIOR} mais antigos omitidos`)
          const unit = p.latest.unit || p.analyte.unit
          return [
            cell(p.analyte.name),
            cell(unit),
            referenceText(p.latest, ''),
            `${dayLabel(p.latest)}: ${valueText(p.latest)}${p.latest.review_status === 'needs_review' ? ' (?)' : ''}`,
            flagText(p.latest.flag),
            shown.length ? shown.join('; ') : '—',
          ]
        }),
      ),
      '',
    )
  }

  lines.push(
    '## Notas',
    '',
    '- Valores na unidade padrão de cada exame; datas no formato AAAA-MM-DD (data da coleta).',
    '- A referência e a situação do último resultado são as impressas no laudo que o gerou.',
    '- `(?)` indica valor extraído automaticamente e ainda não conferido pelo paciente.',
    '- `<`, `>`, `≤`, `≥` antes de um valor indicam resultado limite informado pelo laboratório.',
  )
  return `${lines.join('\n').trimEnd()}\n`
}

export function reportFilename(now: Date = new Date()): string {
  return `relatorio-exames-${isoDay(now.toISOString())}.md`
}
