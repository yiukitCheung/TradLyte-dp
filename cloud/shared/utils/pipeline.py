"""
RDS-side OHLCV pipeline helpers (planner + ingest Lambdas in VPC).

Extracted from the former monolithic daily_ohlcv_fetcher so fetch can stay
off-VPC while ingest/planning talk to RDS.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, List

import pytz

from shared.models.data_models import OHLCVData

if TYPE_CHECKING:
    from shared.clients.rds_timescale_client import RDSTimescaleClient

logger = logging.getLogger(__name__)


def get_missing_dates(rds_client: RDSTimescaleClient, max_days_back: int = 30) -> List[date]:
    """
    Find missing trading dates using the watermark table (metadata-driven).
    """
    try:
        logger.info("Checking watermark table for missing dates...")

        query = """
            SELECT
                MAX(latest_date) as max_date,
                MIN(latest_date) as min_date,
                COUNT(DISTINCT symbol) as symbol_count
            FROM data_ingestion_watermark
            WHERE is_current = TRUE;
        """

        result = rds_client.execute_query(query)

        eastern = pytz.timezone("US/Eastern")
        now_et = datetime.now(eastern)
        today_et = now_et.date()

        if not result or result[0]["max_date"] is None:
            logger.info(
                "No watermark data found, starting fresh backfill from %s days ago",
                max_days_back,
            )
            from_date = today_et - timedelta(days=max_days_back)
        else:
            max_date = result[0]["max_date"]
            symbol_count = result[0]["symbol_count"]
            logger.info(
                "Found watermark: %s symbols, latest date: %s",
                symbol_count,
                max_date,
            )
            from_date = max_date + timedelta(days=1)

        market_close_today = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

        if now_et >= market_close_today and now_et.weekday() < 5:
            latest_available_date = today_et
            logger.info("Market closed today at 4 PM ET, fetching up to: %s", latest_available_date)
        else:
            latest_available_date = today_et - timedelta(days=1)
            if now_et.weekday() >= 5:
                logger.info("Weekend - fetching up to: %s", latest_available_date)
            else:
                logger.info("Before market close (4 PM ET) - fetching up to: %s", latest_available_date)

        if from_date > latest_available_date:
            logger.info("Data is up to date (latest: %s)", from_date - timedelta(days=1))
            return []

        missing_dates: List[date] = []
        current_date = from_date
        while current_date <= latest_available_date:
            if current_date.weekday() < 5:
                missing_dates.append(current_date)
            current_date += timedelta(days=1)

        if len(missing_dates) > max_days_back:
            logger.warning(
                "Found %s missing dates, limiting to %s most recent",
                len(missing_dates),
                max_days_back,
            )
            missing_dates = missing_dates[-max_days_back:]

        logger.info("Missing dates to fetch: %s", len(missing_dates))
        return missing_dates

    except Exception as e:
        logger.error("Error querying watermark table: %s", e)
        logger.warning("Falling back to latest available date based on market time")

        eastern = pytz.timezone("US/Eastern")
        now_et = datetime.now(eastern)
        today_et = now_et.date()
        market_close_today = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

        if now_et >= market_close_today and now_et.weekday() < 5:
            fallback_date = today_et
        else:
            fallback_date = today_et - timedelta(days=1)

        logger.info("Fallback date: %s", fallback_date)
        return [fallback_date] if fallback_date.weekday() < 5 else []


def write_to_rds_with_retention(
    rds_client: RDSTimescaleClient,
    ohlcv_data: List[OHLCVData],
    retention_years: int = 5,
) -> int:
    """Insert OHLCV into RDS with a rolling retention window (years)."""
    if not ohlcv_data:
        return 0

    retention_threshold = date.today() - timedelta(days=365 * retention_years + 30)

    filtered_data = [o for o in ohlcv_data if o.timestamp.date() >= retention_threshold]

    if len(filtered_data) < len(ohlcv_data):
        logger.warning(
            "Filtered out %s records older than %s (outside retention)",
            len(ohlcv_data) - len(filtered_data),
            retention_threshold,
        )

    if not filtered_data:
        logger.info("No records within retention period, skipping RDS insert")
        return 0

    records_inserted = rds_client.insert_ohlcv_data(filtered_data)
    logger.info("Inserted %s records to RDS (retention: %s years)", records_inserted, retention_years)
    return records_inserted


def update_watermark(rds_client: RDSTimescaleClient, symbols: List[str], fetch_date: date) -> None:
    """SCD Type 2 watermark update after successful ingest."""
    try:
        conn = rds_client.connection
        cursor = conn.cursor()

        old_autocommit = conn.autocommit
        conn.autocommit = False

        try:
            for symbol in symbols:
                cursor.execute(
                    """
                    UPDATE data_ingestion_watermark
                    SET is_current = FALSE
                    WHERE symbol = %s AND is_current = TRUE;
                    """,
                    (symbol,),
                )
                cursor.execute(
                    """
                    INSERT INTO data_ingestion_watermark
                        (symbol, latest_date, ingested_at, records_count, is_current)
                    VALUES (%s, %s, NOW(), 1, TRUE);
                    """,
                    (symbol, fetch_date),
                )

            conn.commit()
            logger.info(
                "Updated watermark for %s symbols (date: %s, SCD Type 2)",
                len(symbols),
                fetch_date,
            )

        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.autocommit = old_autocommit

    except Exception as e:
        logger.error("Error updating watermark: %s", e)


def get_new_symbols(rds_client: RDSTimescaleClient, days_threshold: int = 7) -> List[str]:
    """
    Symbols that need historical backfill (not in watermark or limited history).
    days_threshold is kept for API compatibility; selection uses watermark counts.
    """
    _ = days_threshold

    try:
        query_not_in_watermark = """
            SELECT sm.symbol, sm.type, sm.name, 'NO_WATERMARK' as reason
            FROM symbol_metadata sm
            LEFT JOIN data_ingestion_watermark diw
                ON sm.symbol = diw.symbol AND diw.is_current = TRUE
            WHERE diw.symbol IS NULL
              AND LOWER(sm.active) = 'true';
        """

        query_limited_history = """
            SELECT sm.symbol, sm.type, sm.name, 'LIMITED_HISTORY' as reason
            FROM symbol_metadata sm
            JOIN (
                SELECT symbol, COUNT(*) as record_count
                FROM data_ingestion_watermark
                GROUP BY symbol
                HAVING COUNT(*) = 1
            ) counts ON sm.symbol = counts.symbol
            JOIN data_ingestion_watermark diw
                ON sm.symbol = diw.symbol AND diw.is_current = TRUE
            WHERE LOWER(sm.active) = 'true'
              AND diw.records_count <= 5
            ORDER BY sm.type, sm.symbol;
        """

        result_no_watermark = rds_client.execute_query(query_not_in_watermark) or []
        result_limited = rds_client.execute_query(query_limited_history) or []

        all_results = result_no_watermark + result_limited
        seen_symbols = set()
        new_symbols: List[str] = []

        for row in all_results:
            symbol = row["symbol"]
            if symbol not in seen_symbols:
                seen_symbols.add(symbol)
                new_symbols.append(symbol)

        reasons = {}
        for row in all_results:
            reason = row.get("reason", "UNKNOWN")
            reasons[reason] = reasons.get(reason, 0) + 1

        logger.info("Symbols needing backfill: %s; by reason: %s", len(new_symbols), reasons)
        return new_symbols

    except Exception as e:
        logger.error("Error finding new symbols: %s", e)
        return []
