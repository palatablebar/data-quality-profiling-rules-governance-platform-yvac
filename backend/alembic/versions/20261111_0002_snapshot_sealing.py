"""snapshot sealing: run fingerprints, rule-param snapshots, soft-deleted rules

Revision ID: 20261111_0002
Revises: 20261109_0001
Create Date: 2026-11-11

Every successful batch seals the data fingerprint of the scanned file, the
enforced rule definitions (incl. params) and the violation evidence, so that
later rule edits/deletions and same-name re-uploads only affect new batches.

This migration is defensive: revision 20261109_0001 bootstraps from the
*current* model metadata, so on a fresh database these columns already exist;
the runtime schema patcher (app/config/schema_patch.py) may also have applied
them first. Every statement is therefore guarded by an information_schema
check, and backfills run only for rows this migration itself just exposed.
"""
from alembic import op
from sqlalchemy import text

revision = "20261111_0002"
down_revision = "20261109_0001"
branch_labels = None
depends_on = None

COLUMNS = {
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

INDEXES = {
    "rule_runs": [("ix_rule_runs_dataset_checksum", "dataset_checksum")],
    "quality_rules": [
        ("ix_quality_rules_is_deleted", "is_deleted"),
        ("ix_rule_name_dataset", "dataset_id, name"),
    ],
}

DROP_INDEXES = {"quality_rules": ["uq_rule_name_dataset"]}


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table},
    )
    return {row[0] for row in rows}


def _indexes(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table},
    )
    return {row[0] for row in rows}


def upgrade() -> None:
    conn = op.get_bind()
    added: set[tuple[str, str]] = set()
    for table, columns in COLUMNS.items():
        existing = _columns(conn, table)
        for column, ddl in columns:
            if column in existing:
                continue
            op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.add((table, column))
    existing_indexes = _indexes(conn, "quality_rules")
    for name in DROP_INDEXES["quality_rules"]:
        if name in existing_indexes:
            op.execute(f"ALTER TABLE quality_rules DROP INDEX {name}")
    for table, indexes in INDEXES.items():
        existing_indexes = _indexes(conn, table)
        for name, columns in indexes:
            if name not in existing_indexes:
                op.execute(f"CREATE INDEX {name} ON {table} ({columns})")

    # ---- backfill historical rows from the live tables (only for rows this
    # migration just exposed — never rewrite snapshots frozen later) ----
    if ("rule_runs", "sealed_at") in added:
        op.execute(
            "UPDATE rule_runs r JOIN datasets d ON r.dataset_id = d.id "
            "SET r.dataset_name = d.name, r.dataset_format = d.source_format, "
            "    r.dataset_checksum = d.checksum, r.dataset_row_count = d.row_count, "
            "    r.dataset_column_count = d.column_count, r.dataset_file_size = d.file_size, "
            "    r.dataset_file_path = d.file_path, "
            "    r.sealed_at = COALESCE(r.finished_at, NOW()) "
            "WHERE r.sealed_at IS NULL AND r.status = 'succeeded'"
        )
    if ("rule_run_items", "params_json") in added:
        op.execute(
            "UPDATE rule_run_items i JOIN quality_rules q ON i.rule_id = q.id "
            "SET i.params_json = q.params_json, i.sample_limit = q.sample_limit "
            "WHERE i.params_json IS NULL"
        )
    if ("issues", "severity") in added:
        op.execute(
            "UPDATE issues i JOIN quality_rules q ON i.rule_id = q.id "
            "SET i.severity = q.severity"
        )
    if ("rule_runs", "profile_snapshot_json") in added:
        # same helper as the runtime patcher, bound to the migration connection
        from sqlalchemy.orm import Session

        from app.config.schema_patch import backfill_profile_snapshots

        with Session(bind=conn) as session:
            backfill_profile_snapshots(session)


def downgrade() -> None:
    conn = op.get_bind()
    existing = _columns(conn, "issues")
    if "severity" in existing:
        op.execute("ALTER TABLE issues DROP COLUMN severity")
    existing = _columns(conn, "quality_rules")
    if "is_deleted" in existing:
        op.execute("ALTER TABLE quality_rules DROP COLUMN is_deleted")
    existing_indexes = _indexes(conn, "quality_rules")
    for name in ("ix_rule_name_dataset", "ix_quality_rules_is_deleted"):
        if name in existing_indexes:
            op.execute(f"ALTER TABLE quality_rules DROP INDEX {name}")
    if "uq_rule_name_dataset" not in _indexes(conn, "quality_rules"):
        # dedupe before restoring the unique constraint
        conn.execute(
            text(
                "DELETE q1 FROM quality_rules q1 "
                "JOIN quality_rules q2 ON q1.dataset_id = q2.dataset_id "
                "AND q1.name = q2.name AND q1.id > q2.id"
            )
        )
        op.execute(
            "ALTER TABLE quality_rules ADD CONSTRAINT uq_rule_name_dataset "
            "UNIQUE (dataset_id, name)"
        )
    existing = _columns(conn, "rule_run_items")
    for column in ("sample_limit", "params_json"):
        if column in existing:
            op.execute(f"ALTER TABLE rule_run_items DROP COLUMN {column}")
    existing_indexes = _indexes(conn, "rule_runs")
    if "ix_rule_runs_dataset_checksum" in existing_indexes:
        op.execute("ALTER TABLE rule_runs DROP INDEX ix_rule_runs_dataset_checksum")
    existing = _columns(conn, "rule_runs")
    for column in (
        "profile_snapshot_json",
        "sealed_at",
        "dataset_file_path",
        "dataset_file_size",
        "dataset_column_count",
        "dataset_row_count",
        "dataset_checksum",
        "dataset_format",
        "dataset_name",
    ):
        if column in existing:
            op.execute(f"ALTER TABLE rule_runs DROP COLUMN {column}")
