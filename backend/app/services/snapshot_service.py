"""Seal-time snapshots for executed batches.

When a batch (rule execution or profiling) runs, the exact data fingerprint
of the scanned file is captured at load time; on success the batch is sealed
(``sealed_at``) and, for rule batches, the column-profile appendix is frozen
onto the ``rule_runs`` row. From that moment on the batch is self-contained:
re-uploading a same-name file, editing/disabling/deleting rules or removing
the dataset must not rewrite what an old report shows. Regulators reviewing
a three-month-old batch see the fingerprint, the enforced rules and the
violation results exactly as they were.
"""
import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.constants.rule_run_status import RuleRunStatus
from app.models import ColumnProfile, Dataset, RuleRun
from app.profiling.loader import LoadedFrame
from app.utils import storage
from app.utils.logger import log_event


def stamp_fingerprint(run: RuleRun, dataset: Dataset, loaded: LoadedFrame) -> None:
    """Capture the fingerprint of the file being scanned.

    Called right after the frame is loaded: the checksum is computed from the
    bytes on disk at that moment, so it always describes the data the batch
    actually reads — even if a concurrent re-upload swaps or removes the file
    afterwards. Caller commits; this only mutates the ORM object.
    """
    run.dataset_name = dataset.name
    run.dataset_format = dataset.source_format
    run.dataset_checksum = storage.sha256_file(dataset.file_path)
    run.dataset_row_count = loaded.row_count
    run.dataset_column_count = loaded.column_count
    run.dataset_file_path = dataset.file_path
    try:
        run.dataset_file_size = os.path.getsize(dataset.file_path)
    except OSError:  # pragma: no cover - file vanished mid-run
        run.dataset_file_size = dataset.file_size


def seal_run(db: Session, run: RuleRun, dataset_id: int, with_profile_snapshot: bool = True) -> None:
    """Mark a succeeded batch as sealed and freeze its profile appendix."""
    if with_profile_snapshot:
        run.profile_snapshot_json = profile_appendix(db, dataset_id)
    run.sealed_at = datetime.now()
    log_event(
        "RUN_SEALED",
        run_id=run.id,
        dataset_id=dataset_id,
        checksum=(run.dataset_checksum or "")[:12],
        rows=run.dataset_row_count,
    )


def profile_appendix(db: Session, dataset_id: int) -> list[dict]:
    """Raw column-profile summary used as the report's quantile appendix."""
    profiles = (
        db.query(ColumnProfile)
        .filter(ColumnProfile.dataset_id == dataset_id)
        .order_by(ColumnProfile.ordinal.asc())
        .all()
    )
    return [
        {
            "column_name": profile.column_name,
            "inferred_type": profile.inferred_type,
            "null_rate": float(profile.null_rate or 0),
            "min_value": profile.min_value,
            "max_value": profile.max_value,
            "p50": profile.p50,
            "p95": profile.p95,
            "outlier_count": profile.outlier_count,
        }
        for profile in profiles
    ]


def source_file_referenced(db: Session, checksum: str | None, path: str | None = None) -> bool:
    """True when a source file must be kept: either a sealed batch points at
    its checksum, or an in-flight batch is currently scanning that path.

    Used before deleting a superseded/removed dataset file so historical
    reports stay verifiable against their source data.
    """
    if checksum:
        sealed = db.query(RuleRun.id).filter(
            RuleRun.dataset_checksum == checksum,
            RuleRun.sealed_at.isnot(None),
        )
        if db.query(sealed.exists()).scalar():
            return True
    if path:
        in_flight = db.query(RuleRun.id).filter(
            RuleRun.dataset_file_path == path,
            RuleRun.status.in_([RuleRunStatus.QUEUED.value, RuleRunStatus.RUNNING.value]),
        )
        if db.query(in_flight.exists()).scalar():
            return True
    return False
