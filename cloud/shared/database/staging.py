"""Runtime helpers for scanner staging tables."""

from __future__ import annotations

from pathlib import Path

# Legacy bundled copy (kept as a fallback for deploy artifacts that ship
# database/sql/ but not the db/ catalog yet).
_SQL_PATH = Path(__file__).resolve().parent / "sql" / "daily_scan_signals.sql"


def daily_scan_signals_ddl() -> str:
    """Return the canonical daily_scan_signals DDL + index SQL.

    Source of truth is the shared SQL catalog (``scan_signals.ensure_table``);
    falls back to the bundled ``database/sql/daily_scan_signals.sql`` when the
    db/ catalog is not present in the deployment artifact.
    """
    try:
        try:
            from ..db.catalog import load_sql  # shared/ package present
        except ImportError:  # pragma: no cover - flattened deploy layout
            from db.catalog import load_sql  # type: ignore
        return load_sql("scan_signals.ensure_table")
    except Exception:  # pragma: no cover - fallback to bundled DDL
        return _SQL_PATH.read_text()


def ensure_daily_scan_signals(cursor) -> None:
    """Create the staging table and index if they do not exist."""
    cursor.execute(daily_scan_signals_ddl())
