"""
Daily Scanner AWS Batch Job

 Runs pre-built strategies on all active symbols and writes ranked top picks to RDS.
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


def run_scanner_job(
    scan_date: Optional[date] = None,
    strategy_name: str = "vegas_channel_short_term",
) -> int:
    """
    Run daily scanner job
    
    Args:
        scan_date: Date to scan (default: today)
        strategy_name: Strategy name to run (default: vegas_channel_short_term)
        
    Returns:
        Number of top picks written
    """
    if scan_date is None:
        scan_date = date.today()
    
    logger.info("=" * 80)
    logger.info("🚀 STARTING DAILY SCANNER JOB")
    logger.info("=" * 80)
    logger.info(f"📅 Scan date: {scan_date}")
    logger.info(f"🎯 Strategy name: {strategy_name}")
    logger.info(f"⏰ Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Get configuration
        rds_connection_string = get_rds_connection_string()
        s3_bucket = os.environ.get('S3_BUCKET_NAME')
        use_s3 = os.environ.get('USE_S3', 'false').lower() == 'true'
        
        logger.info("📊 Configuration:")
        logger.info("   RDS: Connected")
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
        symbols = rds_client.get_active_symbols()
        logger.info(f"   Found {len(symbols)} active symbols")
        
        if not symbols:
            logger.warning("⚠️  No active symbols found. Exiting.")
            return 0
        
        # Get strategy metadata and validate strategy_name
        logger.info("🔍 Loading strategy metadata...")
        strategy_metadata = scanner.get_strategy_metadata()
        available_strategy_names = [s.get("strategy_name") for s in strategy_metadata]
        if strategy_name not in available_strategy_names:
            raise ValueError(
                f"Unknown strategy_name '{strategy_name}'. "
                f"Available: {', '.join([n for n in available_strategy_names if n])}"
            )
        logger.info(f"   Loaded {len(strategy_metadata)} strategies")
        
        # Scan symbols
        logger.info("🔎 Scanning symbols...")
        signals = scanner.run(
            symbols=symbols,
            strategy_metadata=strategy_metadata,
            scan_date=scan_date,
            include_strategy_names=[strategy_name],
        )
        
        logger.info(f"   Generated {len(signals)} signals")

        logger.info("🏆 Ranking top picks...")
        ranked = scanner.rank(
            signals,
            by_pick_type=False,
            top_k=10,
            unique_symbol=True,
        )
        total_top_picks = len(ranked)
        logger.info(f"   Selected {total_top_picks} top picks")

        logger.info("💾 Writing top picks to RDS...")
        picks_written = scanner.write(ranked, rds_client, scan_date)
        logger.info(f"   Wrote {picks_written} top picks")
        
        # Close RDS connection
        rds_client.close()
        
        logger.info("=" * 80)
        logger.info("✅ DAILY SCANNER JOB COMPLETED SUCCESSFULLY")
        logger.info("=" * 80)
        logger.info("📊 Summary:")
        logger.info(f"   Symbols scanned: {len(symbols)}")
        logger.info(f"   Strategy run: {strategy_name}")
        logger.info(f"   Signals generated: {len(signals)}")
        logger.info(f"   Top picks written: {picks_written}")
        logger.info(f"⏰ End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return picks_written
        
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
    strategy_name = os.environ.get('STRATEGY_NAME', 'vegas_channel_short_term')
    
    # Parse scan date if provided
    scan_date = None
    if scan_date_str:
        try:
            scan_date = datetime.strptime(scan_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.warning(f"Invalid SCAN_DATE format: {scan_date_str}. Using today.")
    
    logger.info("📋 CONFIGURATION:")
    logger.info(f"   AWS_REGION: {aws_region}")
    logger.info(f"   SCAN_DATE: {scan_date or 'Today (default)'}")
    logger.info(f"   STRATEGY_NAME: {strategy_name}")
    
    try:
        logger.info("\n✅ Starting Daily Scanner...")
        picks_written = run_scanner_job(
            scan_date=scan_date,
            strategy_name=strategy_name,
        )
        
        logger.info(f"\n✅ Scanner completed successfully. Wrote {picks_written} top picks.")
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"\n❌ Scanner failed: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
