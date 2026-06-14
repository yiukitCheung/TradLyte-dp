"""Forward-only schema migration runner.

Applies the ordered ``.sql`` files in ``versions/`` inside a transaction each and
records every applied version in a ``schema_migrations`` table so reruns are
idempotent. Migrations are written defensively (``CREATE ... IF NOT EXISTS``,
``CREATE OR REPLACE``) so the very first run cleanly *adopts* an already-populated
database: the existing objects are left intact and the versions are simply
marked applied.

Usage (locally, through the SSM tunnel, or from a CI step):

    python -m shared.db.migrations.runner --status
    python -m shared.db.migrations.runner            # apply all pending
    python -m shared.db.migrations.runner --dry-run

Credentials come from the shared connection layer (Secrets Manager or env), so
the same ``RDS_SECRET_ARN`` used everywhere else applies here too.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

try:  # package layout (shared.db / db on a zip root)
    from ..connection import connect
except ImportError:  # pragma: no cover - direct script execution fallback
    from shared.db.connection import connect  # type: ignore

logger = logging.getLogger(__name__)

_VERSIONS_DIR = Path(__file__).resolve().parent / "versions"
_VERSION_RE = re.compile(r"^(V\d+)__")

_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(20) PRIMARY KEY,
    filename    TEXT        NOT NULL,
    checksum    CHAR(64)    NOT NULL,
    applied_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_migrations() -> List[Tuple[str, Path]]:
    """Return ``(version, path)`` pairs sorted by version, e.g. ('V001', ...)."""
    found: List[Tuple[str, Path]] = []
    for path in sorted(_VERSIONS_DIR.glob("V*.sql")):
        match = _VERSION_RE.match(path.name)
        if not match:
            logger.warning("Skipping migration with unexpected name: %s", path.name)
            continue
        found.append((match.group(1), path))
    return found


def _applied_versions(cursor) -> set:
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def get_status() -> List[Tuple[str, str, bool]]:
    """Return ``(version, filename, applied)`` for every known migration."""
    conn = connect(autocommit=True)
    try:
        with conn.cursor() as cursor:
            cursor.execute(_TRACKING_DDL)
            applied = _applied_versions(cursor)
    finally:
        conn.close()
    return [(v, p.name, v in applied) for v, p in discover_migrations()]


def run_migrations(dry_run: bool = False, target: Optional[str] = None) -> List[str]:
    """Apply pending migrations. Returns the list of versions applied.

    Args:
        dry_run: Log what would run without executing or recording it.
        target: Stop after applying this version (inclusive).
    """
    conn = connect(autocommit=False)
    applied_now: List[str] = []
    try:
        with conn.cursor() as cursor:
            cursor.execute(_TRACKING_DDL)
        conn.commit()

        with conn.cursor() as cursor:
            already = _applied_versions(cursor)

        for version, path in discover_migrations():
            if version in already:
                logger.info("· %s already applied — skipping", version)
                if target and version == target:
                    break
                continue

            sql = path.read_text()
            if dry_run:
                logger.info("DRY-RUN would apply %s (%s)", version, path.name)
            else:
                logger.info("→ applying %s (%s)", version, path.name)
                with conn.cursor() as cursor:
                    cursor.execute(sql)
                    cursor.execute(
                        "INSERT INTO schema_migrations (version, filename, checksum) "
                        "VALUES (%s, %s, %s)",
                        (version, path.name, _checksum(sql)),
                    )
                conn.commit()
                applied_now.append(version)

            if target and version == target:
                break

        if not applied_now and not dry_run:
            logger.info("Schema is up to date — no migrations applied.")
        return applied_now
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Apply Tradlyte schema migrations")
    parser.add_argument("--status", action="store_true", help="Show applied/pending status and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show pending migrations without applying")
    parser.add_argument("--target", help="Apply up to and including this version (e.g. V003)")
    args = parser.parse_args()

    if args.status:
        print(f"{'VERSION':<8} {'APPLIED':<8} FILENAME")
        for version, filename, applied in get_status():
            print(f"{version:<8} {'yes' if applied else 'no':<8} {filename}")
        return

    applied = run_migrations(dry_run=args.dry_run, target=args.target)
    if args.dry_run:
        print("Dry run complete.")
    else:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")


if __name__ == "__main__":
    main()
