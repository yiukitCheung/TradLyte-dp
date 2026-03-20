"""
Backtest API Lambda Handler

Accepts POST requests with strategy JSON config and returns backtest results.
Endpoint: POST /api/backtest
"""

import json
import os
import logging
import boto3
from datetime import datetime
from typing import Dict, Any, List
from analytics_core.strategies.builder import CompositeStrategy
from analytics_core.executor import MultiTimeframeExecutor
from analytics_core.backtester import Backtester
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
        database = secret.get('database', secret.get('dbname', 'postgres'))
        username = secret.get('username')
        password = secret.get('password')
        
        return f"postgresql://{username}:{password}@{host}:{port}/{database}"
    except Exception as e:
        logger.error(f"Error retrieving RDS credentials: {str(e)}")
        raise

def _collect_timeframes_from_components(components: Dict[str, Any], base_tf: str) -> List[str]:
    """Collect unique timeframes from strategy component dicts (JSON payloads)."""
    tfs = {base_tf}
    if not isinstance(components, dict):
        return list(tfs)
    for comp in components.values():
        if isinstance(comp, dict) and comp.get("timeframe"):
            tfs.add(str(comp["timeframe"]).strip().lower() or base_tf)
    return sorted(tfs)


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
        symbol = str(body['symbol']).strip().upper()
        timeframe = str(body.get('timeframe', '1d')).strip().lower() or '1d'
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
        if start_date > end_date:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'start_date must be on or before end_date'}),
            }
        max_days = int(os.environ.get('BACKTEST_MAX_LOOKBACK_DAYS', '1825'))  # ~5 years
        if (end_date - start_date).days > max_days:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Date range exceeds maximum of {max_days} days (~5 years)',
                }),
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
        
        executor = MultiTimeframeExecutor(rds_connection_string=rds_connection_string)
        timeframes = _collect_timeframes_from_components(components, timeframe)

        try:
            result_df = executor.execute(
                strategy=strategy,
                symbol=symbol,
                timeframes=timeframes,
                start_date=start_date,
                end_date=end_date,
                base_timeframe=timeframe,
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
