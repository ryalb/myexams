export interface WarningItem {
  code: string
  params?: Record<string, unknown>
}

export interface Laboratory {
  id: number
  name: string
}

export interface ExamDocument {
  id: number
  original_filename: string
  status: string
  extraction_method: string
  page_count: number
  laboratory: number | null
  laboratory_name: string
  accession_number: string
  ordering_physician: string
  collected_on: string | null
  issued_at: string | null
  error_code: string
  error_params: Record<string, unknown>
  warnings: WarningItem[]
  result_count: number
  review_count: number
  out_of_range_count: number
  progress_done: number
  progress_total: number
  created_at: string
}

export interface PageText {
  number: number
  method: string
  lines: string[]
}

export interface ExamDocumentDetail extends ExamDocument {
  page_text: PageText[]
}

export interface AnalyteOption {
  id: number
  slug: string
  name: string
  category: string
  kind: string
  unit: string
  is_calculated: boolean
}

export interface ExamResult {
  id: number
  document: number
  analyte: number | null
  analyte_info: { id: number; slug: string; name: string; unit: string } | null
  origin: string
  page: number
  position: number
  name_raw: string
  section_raw: string
  value_raw: string
  unit_raw: string
  reference_raw: string
  flag_printed: string
  value_numeric: string | null
  value_text: string
  unit: string
  value_canonical: string | null
  flag: string
  interpretation: string
  collected_at: string | null
  review_status: string
  warnings: WarningItem[]
  source_line: string
}

export interface AnalyteOverview {
  slug: string
  name: string
  category: string
  unit: string
  kind: string
  count: number
  latestValue: number | null
  latestText: string
  latestAt: string | null
  latestFlag: string
  latestInterpretation: string
  delta: number | null
  deltaPct: number | null
  sparkline: number[]
}

export interface HistoryPoint {
  id: number
  documentId: number
  laboratory: string
  origin: string
  collectedAt: string | null
  value: number | null
  valueRaw: string
  valueText: string
  comparator: string
  unit: string
  refLow: number | null
  refHigh: number | null
  referenceRaw: string
  flag: string
  interpretation: string
  reviewStatus: string
}

export interface AnalyteHistory {
  analyte: { id: number; slug: string; name: string; category: string; unit: string; kind: string }
  chartable: boolean
  points: HistoryPoint[]
}

export interface LlmCallDetail {
  kind: 'header' | 'results' | 'match' | 'vision' | string
  page: number | null
  seconds: number
  load_seconds: number | null
  prompt_tokens: number | null
  output_tokens: number | null
  tokens_per_second: number | null
  outcome: 'ok' | 'timeout' | 'error' | string
}

// Todas as chaves são opcionais: execuções antigas guardaram menos métricas.
export interface RunStats {
  blocks?: number
  rule_blocks?: number
  table_blocks?: number
  llm_calls?: number
  llm_seconds?: number
  llm_skipped_units?: number
  skipped_blocks?: number
  header_by_llm?: boolean
  results?: number
  previous?: number
  needs_review?: number
  llm_calls_detail?: LlmCallDetail[]
  llm_output_tokens?: number
  llm_avg_tokens_per_second?: number | null
  llm_load_seconds?: number
}

export interface ExtractionRun {
  id: number
  status: 'queued' | 'running' | 'success' | 'failed' | string
  model: string
  vision_model: string
  prompt_version: string
  started_at: string | null
  finished_at: string | null
  error_code: string
  error_params: Record<string, unknown>
  stats: RunStats | null
}
