"""
Daily Scanner AWS Batch Job

Runs pre-built strategies on all active symbols and writes signals to daily_signals table.
Triggered daily after data pipeline completes (4:30 PM ET).
"""

import os
import sys
import logging
from datetime import date, datetime
from typing import Optional
import boto3
import json

# Add project root (aws_lambda_architecture) to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../..'))

from shared.analytics_core.scanner import DailyScanner
from shared.clients.rds_timescale_client import RDSTimescaleClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_rds_connection_string() -> str:
    """Get RDS connection string from Secrets Manager"""
    secret_arn = os.environ.get('RDS_SECRET_ARN')
    if not secret_arn:
        raise ValueError("RDS_SECRET_ARN environment variable not set")
    
    secrets_client = boto3.client('secretsmanager', region_name=os.environ.get('AWS_REGION', 'ca-west-1'))
    
    try:
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        secret = json.loads(response['SecretString'])
        
        host = secret.get('host')
        port = secret.get('port', 5432)
        database = secret.get('database', 'postgres')
        username = secret.get('username')
        password = secret.get('password')
        
        return f"postgresql://{username}:{password}@{host}:{port}/{database}"
    except Exception as e:
        logger.error(f"Error retrieving RDS credentials: {str(e)}")
        raise


def run_scanner_job(scan_date: Optional[date] = None) -> int:
    """
    Run daily scanner job
    
    Args:
        scan_date: Date to scan (default: today)
        
    Returns:
        Number of signals generated
    """
    if scan_date is None:
        scan_date = date.today()
    
    logger.info("=" * 80)
    logger.info("🚀 STARTING DAILY SCANNER JOB")
    logger.info("=" * 80)
    logger.info(f"📅 Scan date: {scan_date}")
    logger.info(f"⏰ Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Get configuration
        rds_connection_string = get_rds_connection_string()
        s3_bucket = os.environ.get('S3_BUCKET_NAME')
        use_s3 = os.environ.get('USE_S3', 'false').lower() == 'true'
        
        logger.info(f"📊 Configuration:")
        logger.info(f"   RDS: Connected")
        logger.info(f"   S3 Bucket: {s3_bucket if s3_bucket else 'Not used'}")
        logger.info(f"   Use S3: {use_s3}")
        
        # Initialize scanner
        scanner = DailyScanner(
            rds_connection_string=rds_connection_string,
            s3_bucket=s3_bucket,
            use_s3=use_s3
        )
        
        # Initialize RDS client for symbol loading
        rds_client = RDSTimescaleClient(secret_arn=os.environ.get('RDS_SECRET_ARN'))
        
        # Get active symbols
        logger.info("📋 Loading active symbols...")
        symbols = scanner.get_active_symbols(rds_client)
        logger.info(f"   Found {len(symbols)} active symbols")
        
        if not symbols:
            logger.warning("⚠️  No active symbols found. Exiting.")
            return 0
        
        # Get pre-built strategies
        logger.info("🔍 Loading pick profiles...")
        pick_profiles = scanner.get_pick_profiles()
        logger.info(f"   Loaded {len(pick_profiles)} pick profiles:")
        for profile in pick_profiles:
            logger.info(f"     - {profile['pick_type']} ({profile['strategy_name']}, {profile['term']})")
        
        # Scan symbols
        logger.info("🔎 Scanning symbols with multi-timeframe pick profiles...")
        signals = scanner.run(
            symbols=symbols,
            pick_profiles=pick_profiles,
            scan_date=scan_date,
            rds_client=rds_client
        )
        
        logger.info(f"   Generated {len(signals)} signals")

        logger.info("🏆 Ranking top picks by pick type...")
        ranked = scanner.rank(
            signals,
            by_pick_type=True,
            top_k=10,
            unique_symbol=True,
        )
        total_top_picks = sum(len(v) for v in ranked.values())
        logger.info(f"   Selected {total_top_picks} top picks across {len(ranked)} pick types")

        logger.info("💾 Writing signals and top picks to RDS...")
        total_written = scanner.write(ranked, rds_client, scan_date, all_signals=signals if signals else None)
        signals_written = len(signals) if signals else 0
        picks_written = total_written - signals_written
        logger.info(f"   Wrote {signals_written} signals, {picks_written} top picks")
        
        # Close RDS connection
        rds_client.close()
        
        logger.info("=" * 80)
        logger.info("✅ DAILY SCANNER JOB COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        logger.info(f"📊 Summary:")
        logger.info(f"   Symbols scanned: {len(symbols)}")
        logger.info(f"   Pick profiles run: {len(pick_profiles)}")
        logger.info(f"   Signals generated: {len(signals)}")
        logger.info(f"   Signals written: {signals_written}")
        logger.info(f"   Top picks written: {picks_written}")
        logger.info(f"⏰ End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return written_count
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ FATAL ERROR IN DAILY SCANNER JOB: {str(e)}")
        logger.error("=" * 80, exc_info=True)
        raise


def main():
    """Main entry point for AWS Batch job"""
    logger.info("=" * 80)
    logger.info("AWS BATCH SCANNER STARTUP")
    logger.info("=" * 80)
    logger.info("Starting automated Daily Scanner job")
    
    # Get configuration from environment variables
    aws_region = os.environ.get('AWS_REGION', 'ca-west-1')
    scan_date_str = os.environ.get('SCAN_DATE')  # Optional: override scan date
    
    # Parse scan date if provided
    scan_date = None
    if scan_date_str:
        try:
            scan_date = datetime.strptime(scan_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.warning(f"Invalid SCAN_DATE format: {scan_date_str}. Using today.")
    
    logger.info(f"📋 CONFIGURATION:")
    logger.info(f"   AWS_REGION: {aws_region}")
    logger.info(f"   SCAN_DATE: {scan_date or 'Today (default)'}")
    
    try:
        logger.info("\n✅ Starting Daily Scanner...")
        signals_written = run_scanner_job(scan_date=scan_date)
        
        logger.info(f"\n✅ Scanner completed successfully. Wrote {signals_written} signals.")
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"\n❌ Scanner failed: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
