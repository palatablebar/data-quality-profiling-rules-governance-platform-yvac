from app.schemas.common import iso
from app.utils.formatters import fmt_duration, fmt_int, rule_param_summary


def serialize_run_item(item) -> dict:
    params = item.params_json or {}
    return {
        "id": item.id,
        "rule_id": item.rule_id,
        "rule_name": item.rule_name,
        "rule_type": item.rule_type,
        "column_name": item.column_name,
        "severity": item.severity,
        "params": params,
        "param_summary": rule_param_summary(item.rule_type, params),
        "sample_limit": item.sample_limit,
        "passed": item.passed,
        "violation_count": item.violation_count,
        "persisted_count": item.persisted_count,
        "duration_ms": item.duration_ms,
        "duration_display": fmt_duration(item.duration_ms),
        "error_code": item.error_code,
    }


def serialize_run(run, dataset=None, with_items: bool = False) -> dict:
    # Sealed fingerprint wins; the live dataset row is only a fallback for
    # batches executed before snapshot sealing existed.
    payload = {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "dataset_name": run.dataset_name or (dataset.name if dataset else None),
        "dataset_format": run.dataset_format or (dataset.source_format if dataset else None),
        "kind": run.kind,
        "triggered_by": run.triggered_by,
        "status": run.status,
        "rules_total": run.rules_total,
        "rules_passed": run.rules_passed,
        "rules_failed": run.rules_failed,
        "rows_scanned": run.rows_scanned,
        "rows_scanned_display": fmt_int(run.rows_scanned),
        "duration_ms": run.duration_ms,
        "duration_display": fmt_duration(run.duration_ms),
        "report_no": run.report_no,
        "error_code": run.error_code,
        # ---- sealed data fingerprint (None until the batch succeeds) ----
        "sealed": run.sealed_at is not None,
        "sealed_at": iso(run.sealed_at),
        "dataset_checksum": run.dataset_checksum or (dataset.checksum if dataset else None),
        "dataset_row_count": run.dataset_row_count if run.dataset_row_count is not None else (dataset.row_count if dataset else None),
        "dataset_column_count": run.dataset_column_count if run.dataset_column_count is not None else (dataset.column_count if dataset else None),
        "dataset_file_size": run.dataset_file_size if run.dataset_file_size is not None else (dataset.file_size if dataset else None),
        "dataset_file_path": run.dataset_file_path,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "created_at": iso(run.created_at),
    }
    if with_items:
        payload["items"] = [serialize_run_item(item) for item in run.items]
    return payload
