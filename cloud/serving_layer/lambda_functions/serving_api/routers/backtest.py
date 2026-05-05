"""Backtest API route (thin proxy to dedicated backtester Lambda)."""

import json
import os
from datetime import date
from typing import Any, Dict

import boto3
from botocore.config import Config
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/backtest", tags=["backtest"])

_lambda_client = boto3.client(
    "lambda",
    config=Config(connect_timeout=5, read_timeout=120, retries={"max_attempts": 1, "mode": "standard"}),
)


def _validate_payload(payload: Dict[str, Any]) -> None:
    required_fields = ["strategy_name", "symbol", "components", "start_date", "end_date"]
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")

    strategy_name = str(payload["strategy_name"]).strip()
    symbol = str(payload["symbol"]).strip().upper()
    components = payload.get("components")
    if not strategy_name:
        raise HTTPException(status_code=400, detail="strategy_name must not be empty")
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol must not be empty")
    if not isinstance(components, dict) or not components:
        raise HTTPException(status_code=400, detail="components must be a non-empty object")

    try:
        start_date = date.fromisoformat(str(payload["start_date"]))
        end_date = date.fromisoformat(str(payload["end_date"]))
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date/end_date must be YYYY-MM-DD")

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date")

    max_days = int(os.environ.get("BACKTEST_MAX_LOOKBACK_DAYS", "1825"))
    if (end_date - start_date).days > max_days:
        raise HTTPException(
            status_code=400,
            detail=f"Date range exceeds maximum of {max_days} days (~5 years)",
        )

    initial_capital_raw = payload.get("initial_capital", 10000.0)
    try:
        initial_capital = float(initial_capital_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="initial_capital must be numeric")
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be > 0")


def _invoke_backtester_lambda(payload: Dict[str, Any]) -> Dict[str, Any]:
    function_name = os.environ.get("BACKTEST_FUNCTION_NAME", "dev-serving-backtester")
    invoke_payload = {"body": json.dumps(payload)}
    try:
        response = _lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(invoke_payload).encode("utf-8"),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to invoke backtester Lambda: {exc}")

    if response.get("FunctionError"):
        raise HTTPException(status_code=502, detail=f"Backtester Lambda error: {response.get('FunctionError')}")

    raw = response.get("Payload").read()
    outer = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    status_code = int(outer.get("statusCode", 500))
    body_raw = outer.get("body", {})
    body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
    if status_code >= 400:
        msg = body.get("error") if isinstance(body, dict) else str(body)
        raise HTTPException(status_code=status_code, detail=msg or "Backtester request failed")

    if isinstance(body, dict) and "data" in body and "meta" in body:
        return body

    return {
        "data": body,
        "meta": {
            "symbol": str(payload.get("symbol", "")).strip().upper() or None,
            "strategy_name": payload.get("strategy_name"),
            "timeframe": payload.get("timeframe", "1d"),
            "start_date": payload.get("start_date"),
            "end_date": payload.get("end_date"),
            "source": function_name,
        },
    }


@router.post("")
def run_backtest(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate request and proxy execution to dedicated backtester Lambda."""
    _validate_payload(payload)
    return _invoke_backtester_lambda(payload)
