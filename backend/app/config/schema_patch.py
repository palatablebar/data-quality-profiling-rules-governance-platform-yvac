"""Idempotent schema patcher for MySQL deployments that boot with create_all.

``Base.metadata.create_all`` only creates missing tables — it never alters
existing ones. The snapshot-sealing columns (and relaxing the quality_rules
name uniqueness for soft delete) must therefore be applied explicitly on
startup. Every statement is guarded by an information_schema check, so on a
fresh database — where create_all already did the work — this is a no-op.

Backfills run only for rows that predate the patch (detected by "we just
added this column"), so re-running never rewrites snapshots frozen later.
"""
from sqlalchemy import text

from app.utils.logger import log_event

# table -> [(column, DDL type)]
COLUMN_PATCHES = {
    "rule_runs": [
        ("dataset_name", "VARCHAR(200) NULL"),
        ("dataset_format", "VARCHAR(16) NULL"),
        ("dataset_checksum", "VARCHAR(64) NULL"),
        ("dataset_row_count", "INT NULL"),
        ("dataset_column_count", "INT NULL"),
        ("dataset_file_size", "BIGINT NULL"),
        ("dataset_file_path", "VARCHAR(500) NULL"),
        ("sealed_at", "DATETIME NULL"),
        ("profile_snapshot_json", "JSON NULL"),
    ],
    "rule_run_items": [
        ("params_json", "JSON NULL"),
        ("sample_limit", "INT NOT NULL DEFAULT 200"),
    ],
    "quality_rules": [
        ("is_deleted", "TINYINT(1) NOT NULL DEFAULT 0"),
    ],
    "issues": [
        ("severity", "VARCHAR(16) NOT NULL DEFAULT 'medium'"),
    ],
}

# table -> [(index_name, columns)] created only when missing
INDEX_PATCHES = {
    "rule_runs": [("ix_rule_runs_dataset_checksum", "dataset_checksum")],
    "quality_rules": [
        ("ix_quality_rules_is_deleted", "is_deleted"),
        ("ix_rule_name_dataset", "dataset_id, name"),
    ],
}

# unique indexes that must become non-unique so soft-deleted rows don't block
# name reuse (uniqueness among live rules is enforced in the service layer)
DROP_INDEXES = {"quality_rules": ["uq_rule_name_dataset"]}


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table},
    )
    return {row[0] for row in rows}


def _existing_indexes(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table},
    )
    return {row[0] for row in rows}


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
            ),
            {"table": table},
        ).scalar()
    )


def _backfill_sealed_runs(conn) -> None:
    """Legacy succeeded batches get a fingerprint copied from the live dataset
    row (best available evidence for pre-snapshot executions)."""
    result = conn.execute(
        text(
            "UPDATE rule_runs r JOIN datasets d ON r.dataset_id = d.id "
            "SET r.dataset_name = d.name, r.dataset_format = d.source_format, "
            "    r.dataset_checksum = d.checksum, r.dataset_row_count = d.row_count, "
            "    r.dataset_column_count = d.column_count, r.dataset_file_size = d.file_size, "
            "    r.dataset_file_path = d.file_path, "
            "    r.sealed_at = COALESCE(r.finished_at, NOW()) "
            "WHERE r.sealed_at IS NULL AND r.status = 'succeeded'"
        )
    )
    if result.rowcount:
        log_event("SCHEMA_PATCH_BACKFILL", table="rule_runs", rows=result.rowcount)


def backfill_profile_snapshots(db) -> int:
    """Freeze the current column profiles as the quantile appendix of legacy
    rule batches, so later re-profiling cannot rewrite their reports.

    Takes any Session: the startup patch passes a fresh SessionLocal (after
    its DDL transaction committed), the Alembic migration passes one bound to
    the migration connection. Caller commits.
    """
    from app.models import RuleRun
    from app.services.snapshot_service import profile_appendix

    legacy = (
        db.query(RuleRun)
        .filter(
            RuleRun.status == "succeeded",
            RuleRun.kind == "rules",
            RuleRun.profile_snapshot_json.is_(None),
        )
        .all()
    )
    for run in legacy:
        run.profile_snapshot_json = profile_appendix(db, run.dataset_id)
    return len(legacy)


def _backfill_profile_snapshots_standalone() -> None:
    from app.config.database import SessionLocal

    db = SessionLocal()
    try:
        count = backfill_profile_snapshots(db)
        if count:
            db.commit()
            log_event("SCHEMA_PATCH_BACKFILL", table="rule_runs.profile_snapshot", rows=count)
    finally:
        db.close()


def apply_schema_patch(engine) -> None:
    if engine.dialect.name != "mysql":
        return  # non-MySQL deployments are fresh create_all databases
    added: set[tuple[str, str]] = set()
    with engine.begin() as conn:
        for table, columns in COLUMN_PATCHES.items():
            if not _table_exists(conn, table):
                continue
            existing = _existing_columns(conn, table)
            for column, ddl in columns:
                if column in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                added.add((table, column))
                log_event("SCHEMA_PATCH_COLUMN", table=table, column=column)
        for table, names in DROP_INDEXES.items():
            if not _table_exists(conn, table):
                continue
            existing_indexes = _existing_indexes(conn, table)
            for name in names:
                if name in existing_indexes:
                    conn.execute(text(f"ALTER TABLE {table} DROP INDEX {name}"))
                    log_event("SCHEMA_PATCH_DROP_INDEX", table=table, index=name)
        for table, indexes in INDEX_PATCHES.items():
            if not _table_exists(conn, table):
                continue
            existing_indexes = _existing_indexes(conn, table)
            for name, columns in indexes:
                if name not in existing_indexes:
                    conn.execute(text(f"CREATE INDEX {name} ON {table} ({columns})"))
                    log_event("SCHEMA_PATCH_INDEX", table=table, index=name)
        # Backfills — only for data that predates this patch.
        if ("rule_runs", "sealed_at") in added:
            _backfill_sealed_runs(conn)
        if ("rule_run_items", "params_json") in added:
            result = conn.execute(
                text(
                    "UPDATE rule_run_items i JOIN quality_rules q ON i.rule_id = q.id "
                    "SET i.params_json = q.params_json, i.sample_limit = q.sample_limit "
                    "WHERE i.params_json IS NULL"
                )
            )
            if result.rowcount:
                log_event("SCHEMA_PATCH_BACKFILL", table="rule_run_items", rows=result.rowcount)
        if ("issues", "severity") in added:
            result = conn.execute(
                text(
                    "UPDATE issues i JOIN quality_rules q ON i.rule_id = q.id "
                    "SET i.severity = q.severity"
                )
            )
            if result.rowcount:
                log_event("SCHEMA_PATCH_BACKFILL", table="issues", rows=result.rowcount)
    if ("rule_runs", "profile_snapshot_json") in added:
        _backfill_profile_snapshots_standalone()
    if added:
        log_event("SCHEMA_PATCH_APPLIED", columns=len(added))
