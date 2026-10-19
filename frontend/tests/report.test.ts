// `bun test`: gerador do relatório em Markdown. Dados inventados.
import { describe, expect, test } from 'bun:test'
import { buildReportMarkdown, type ReportAnalyte, type ReportData, type ReportPoint } from '../utils/report'

const labels = {
  category: (code: string) => ({ glycemia: 'Glicemia', lipids: 'Lipidograma' })[code] ?? code,
  sex: (code: string) => ({ male: 'Masculino', female: 'Feminino' })[code] ?? code,
}
const NOW = new Date('2026-10-19T12:00:00Z')

function point(day: string | null, value: number | null, extra: Partial<ReportPoint> = {}): ReportPoint {
  return {
    collected_at: day ? `${day}T12:00:00-03:00` : null,
    origin: 'current',
    value,
    value_raw: value === null ? '' : String(value),
    value_text: '',
    comparator: 'eq',
    unit: 'mg/dL',
    ref_low: 70,
    ref_high: 99,
    reference_raw: '70 a 99 mg/dL',
    flag: value !== null && value > 99 ? 'high' : 'normal',
    review_status: 'auto',
    ...extra,
  }
}

function analyte(name: string, points: ReportPoint[], extra: Partial<ReportAnalyte> = {}): ReportAnalyte {
  return { slug: name.toLowerCase(), name, category: 'glycemia', unit: 'mg/dL', kind: 'numeric', points, ...extra }
}

function data(analytes: ReportAnalyte[]): ReportData {
  return {
    generated_at: '2026-10-19T15:00:00Z',
    profile: { sex: 'male', age: 49 },
    analytes,
  }
}

const build = (d: ReportData, options: Partial<{ range: 'all' | '1y' | '3y' | '5y'; includePrompt: boolean }> = {}) =>
  buildReportMarkdown(d, labels, { range: 'all', includePrompt: false, now: NOW, ...options })

describe('buildReportMarkdown', () => {
  test('cabeçalho traz só sexo e idade', () => {
    const md = build(data([analyte('Glicose', [point('2026-08-25', 90)])]))
    expect(md).toContain('# Relatório de exames laboratoriais')
    expect(md).toContain('- Paciente: sexo masculino, 49 anos')
    expect(md).toContain('- Conteúdo: 1 exames, 1 resultados')
  })

  test('sem resultados, avisa e não monta tabelas', () => {
    const md = build(data([]))
    expect(md).toContain('Nenhum resultado no período.')
    expect(md).not.toContain('## Resumo')
  })

  test('resume o que está fora da referência e a variação', () => {
    const md = build(data([analyte('Glicose', [point('2025-01-10', 90), point('2026-08-25', 120)])]))
    expect(md).toContain('Exames com o último resultado fora da referência: 1 de 1')
    expect(md).toContain('| Glicose | 2026-08-25 | 120 mg/dL | 70 a 99 mg/dL | ALTO | +33.3% |')
    expect(md).toContain('### Maiores variações')
  })

  test('histórico anterior em ordem do mais recente ao mais antigo', () => {
    const md = build(
      data([analyte('Glicose', [point('2024-01-01', 85), point('2025-01-01', 105), point('2026-01-01', 92)])]),
    )
    expect(md).toContain('| 2026-01-01: 92 | normal | 2025-01-01: 105 (ALTO); 2024-01-01: 85 |')
  })

  test('período corta resultados antigos e exame sem nada no período some', () => {
    const md = build(
      data([
        analyte('Glicose', [point('2020-01-01', 80), point('2026-05-01', 90)]),
        analyte('Colesterol', [point('2019-01-01', 190)], { category: 'lipids' }),
      ]),
      { range: '1y' },
    )
    expect(md).toContain('2026-05-01: 90')
    expect(md).not.toContain('2020-01-01')
    expect(md).not.toContain('Colesterol')
    expect(md).toContain('- Período: último ano')
  })

  test('anteriores impressos repetidos não duplicam o resultado do laudo', () => {
    const md = build(
      data([
        analyte('Glicose', [
          point('2025-01-10', 90, { origin: 'lab_previous' }),
          point('2025-01-10', 90),
          point('2026-08-25', 95),
        ]),
      ]),
    )
    expect(md.match(/2025-01-10: 90/g)?.length).toBe(1)
  })

  test('exame qualitativo mostra o texto e valor limite mostra o comparador', () => {
    const md = build(
      data([
        analyte('Urina tipo I', [point('2026-08-25', null, { value_text: 'Ausente', flag: 'unknown', ref_low: null, ref_high: null, reference_raw: 'Ausente' })], {
          kind: 'qualitative',
        }),
        analyte('PCR', [point('2026-08-25', 0.5, { comparator: 'lt' })], { category: 'lipids' }),
      ]),
    )
    expect(md).toContain('Ausente')
    expect(md).toContain('<0.5')
  })

  test('pipe e quebra de linha no texto não quebram a tabela', () => {
    const md = build(data([analyte('A | B', [point('2026-08-25', null, { value_text: 'x|\ny', ref_low: null, ref_high: null, reference_raw: 'a|b' })])]))
    expect(md).toContain('| A / B |')
    expect(md).toContain('x/ y')
  })

  test('instruções para o modelo só entram quando pedidas', () => {
    const d = data([analyte('Glicose', [point('2026-08-25', 90)])])
    expect(build(d)).not.toContain('## Instruções para a análise')
    expect(build(d, { includePrompt: true })).toContain('## Instruções para a análise')
  })

  test('nunca vaza laboratório nem identificação', () => {
    const d = data([analyte('Glicose', [{ ...point('2026-08-25', 90), laboratory: 'Lab Secreto', document_id: 77 } as ReportPoint])])
    const md = build(d, { includePrompt: true })
    expect(md).not.toContain('Secreto')
    expect(md).not.toContain('77')
  })
})
