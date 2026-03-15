"""
Backtest API Lambda Handler

Accepts POST requests with strategy JSON config and returns backtest results.
Endpoint: POST /api/backtest
"""

import json
import os
import logging
import boto3
from datetime import date, datetime
from typing import Dict, Any, Optional

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager', region_name=os.environ.get('AWS_REGION', 'ca-west-1'))


def get_rds_connection_string() -> str:
    """Get RDS connection string from Secrets Manager"""
    secret_arn = os.environ.get('RDS_SECRET_ARN')
    if not secret_arn:
        raise ValueError("RDS_SECRET_ARN environment variable not set")
    
    try:
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        secret = json.loads(response['SecretString'])
        
        # Build connection string
        host = secret.get('host')
        port = secret.get('port', 5432)
        database = secret.get('database', 'postgres')
        username = secret.get('username')
        password = secret.get('password')
        
        return f"postgresql://{username}:{password}@{host}:{port}/{database}"
    except Exception as e:
        logger.error(f"Error retrieving RDS credentials: {str(e)}")
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for backtest API
    
    Expected event body (JSON):
    {
        "strategy_name": "Momentum_Swing",
        "symbol": "AAPL",
        "timeframe": "1d",
        "start_date": "2020-01-01",
        "end_date": "2024-12-31",
        "initial_capital": 10000,
        "components": {
            "setup": { ... },
            "trigger": { ... },
            "exit": { ... }
        }
    }
    
    Returns:
        {
            "statusCode": 200,
            "body": {
                "total_return": 0.25,
                "win_rate": 0.65,
                "max_drawdown": -0.15,
                "sharpe_ratio": 1.2,
                "equity_curve": [...],
                "trades": [...]
            }
        }
    """
    try:
        # Parse request body
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', {})
        
        # Validate required fields
        required_fields = ['strategy_name', 'symbol', 'components']
        for field in required_fields:
            if field not in body:
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'error': f'Missing required field: {field}'
                    })
                }
        
        # Extract parameters
        strategy_name = body['strategy_name']
        symbol = body['symbol']
        timeframe = body.get('timeframe', '1d')
        start_date_str = body.get('start_date')
        end_date_str = body.get('end_date')
        initial_capital = body.get('initial_capital', 10000.0)
        components = body['components']
        
        # Parse dates
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None
        
        if not start_date or not end_date:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'start_date and end_date are required'
                })
            }
        
        # Import analytics core (assumes it's installed in Lambda layer or package)
        try:
            from analytics_core.strategies.builder import CompositeStrategy
            from analytics_core.executor import MultiTimeframeExecutor
            from analytics_core.backtester import Backtester
        except ImportError as e:
            logger.error(f"Error importing analytics_core: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Analytics core not available. Ensure analytics_core is installed.'
                })
            }
        
        # Get RDS connection string
        try:
            rds_connection_string = get_rds_connection_string()
        except Exception as e:
            logger.error(f"Error getting RDS connection: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Failed to connect to database'
                })
            }
        
        # Build strategy from requirements JSON
        strategy_config = {
            'strategy_name': strategy_name,
            'symbol': symbol,
            'timeframe': timeframe,
            'components': components
        }
        
        try:
            strategy = CompositeStrategy.from_requirements_json(strategy_config)
        except Exception as e:
            logger.error(f"Error building strategy: {str(e)}")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Invalid strategy configuration: {str(e)}'
                })
            }
        
        # Initialize executor and load data
        executor = MultiTimeframeExecutor(
            rds_connection_string=rds_connection_string,
            s3_bucket=os.environ.get('S3_BUCKET_NAME')
        )
        
        # Determine required timeframes from components
        timeframes = set(['1d'])  # Base timeframe
        for component in components.values():
            if hasattr(component, 'timeframe'):
                timeframes.add(component.get('timeframe', '1d'))
        timeframes = list(timeframes)
        
        # Execute strategy
        try:
            result_df = executor.execute_strategy(
                strategy=strategy,
                symbol=symbol,
                timeframes=timeframes,
                start_date=start_date,
                end_date=end_date,
                base_timeframe=timeframe,
                use_s3=False  # Use RDS for Lambda (faster)
            )
        except Exception as e:
            logger.error(f"Error executing strategy: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': f'Strategy execution failed: {str(e)}'
                })
            }
        
        # Run backtest
        backtester = Backtester(initial_capital=initial_capital)
        
        # Extract exit parameters from components
        exit_component = components.get('exit', {})
        stop_loss_pct = None
        take_profit_pct = None
        
        if exit_component.get('type') == 'CONDITIONAL_OR_FIXED':
            conditions = exit_component.get('conditions', [])
            for cond in conditions:
                if cond.get('type') == 'STOP_LOSS_PCT':
                    stop_loss_pct = cond.get('value')
                elif cond.get('type') == 'TAKE_PROFIT_PCT':
                    take_profit_pct = cond.get('value')
        elif exit_component.get('type') == 'STOP_LOSS_PCT':
            stop_loss_pct = exit_component.get('value')
        elif exit_component.get('type') == 'TAKE_PROFIT_PCT':
            take_profit_pct = exit_component.get('value')
        
        try:
            backtest_result = backtester.run(
                strategy=strategy,
                data=result_df,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct
            )
        except Exception as e:
            logger.error(f"Error running backtest: {str(e)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': f'Backtest failed: {str(e)}'
                })
            }
        
        # Return results
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'  # CORS for frontend
            },
            'body': json.dumps(backtest_result.to_dict())
        }
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal server error: {str(e)}'
            })
        }
