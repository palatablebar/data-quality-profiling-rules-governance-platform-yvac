export interface RunItem {
  id: number
  rule_id: number | null
  rule_name: string
  rule_type: string
  column_name: string
  severity: string
  params: Record<string, unknown>
  param_summary: string
  sample_limit: number
  rule_deleted?: boolean
  passed: boolean
  violation_count: number
  persisted_count: number
  duration_ms: number
  duration_display: string
  error_code: string | null
}

export interface RuleRun {
  id: number
  dataset_id: number
  dataset_name: string | null
  dataset_format: string | null
  kind: string
  triggered_by: number | null
  status: string
  rules_total: number
  rules_passed: number
  rules_failed: number
  rows_scanned: number
  rows_scanned_display: string
  duration_ms: number
  duration_display: string
  report_no: string | null
  error_code: string | null
  /** sealed data fingerprint — frozen when the batch succeeded */
  sealed: boolean
  sealed_at: string | null
  dataset_checksum: string | null
  dataset_row_count: number | null
  dataset_column_count: number | null
  dataset_file_size: number | null
  dataset_file_path: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

export interface RunDetail extends RuleRun {
  items: RunItem[]
  issue_count: number
}

export interface ReportPayload {
  report_no: string
  generated_at: string
  sealed_at: string | null
  dataset: Record<string, string | number | boolean | null>
  batch: RuleRun
  rules: Array<RunItem & { params: Record<string, unknown>; param_summary: string; rule_deleted: boolean }>
  quantiles: Array<Record<string, string | number | null>>
  violation_samples: Array<Record<string, string | number | null>>
  summary: Record<string, number>
}

export interface QuotaSnapshotState {
  quota: number
  used: number
  remaining: number
}

export interface RunLockState {
  locked: boolean
  dataset_id: number | null
}
