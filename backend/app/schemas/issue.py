from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import iso
from app.utils.formatters import severity_label

IssueStatusLiteral = Literal["open", "triaged", "fixed", "ignored"]


class IssueUpdate(BaseModel):
    status: IssueStatusLiteral | None = None
    assignee_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)


class BulkAssignRequest(BaseModel):
    issue_ids: list[int] = Field(min_length=1)
    assignee_id: int | None = None
    status: IssueStatusLiteral | None = None


def serialize_issue(issue, rule=None, assignee=None, run=None) -> dict:
    rule = rule if rule is not None else getattr(issue, "rule", None)
    assignee = assignee if assignee is not None else getattr(issue, "assignee", None)
    run = run if run is not None else getattr(issue, "run", None)
    dataset_name = None
    report_no = None
    if run is not None:
        report_no = getattr(run, "report_no", None)
        dataset = getattr(run, "dataset", None)
        dataset_name = dataset.name if dataset is not None else None
    # Severity comes from the evidence snapshot; the live rule row (which may
    # have been edited since) is only a fallback for pre-snapshot issues.
    severity = getattr(issue, "severity", None) or (rule.severity if rule else "medium")
    rule_name = rule.name if rule else None
    rule_deleted = bool(getattr(rule, "is_deleted", False)) if rule else False
    return {
        "id": issue.id,
        "rule_run_id": issue.rule_run_id,
        "rule_id": issue.rule_id,
        "rule_name": rule_name,
        "rule_type": rule.rule_type if rule else None,
        "rule_deleted": rule_deleted,
        "severity": severity,
        "severity_label": severity_label(severity),
        "column_name": issue.column_name,
        "row_index": issue.row_index,
        "observed_value": issue.observed_value,
        "expected_expr": issue.expected_expr,
        "status": issue.status,
        "assignee_id": issue.assignee_id,
        "assignee_name": assignee.display_name if assignee else None,
        "note": issue.note,
        "dataset_name": dataset_name,
        "report_no": report_no,
        "created_at": iso(issue.created_at),
        "repro_snippet": f"-- {issue.column_name} row_index={issue.row_index} observed={issue.observed_value!r}",
    }
