"""Pydantic response models for serving endpoints."""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class QuoteRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[int] = None
    type: Optional[str] = None
    primary_exchange: Optional[str] = None
    as_of_date: Optional[date] = None
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    volume: Optional[int] = None


class PickRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scan_date: date
    rank: int
    symbol: str
    strategy_name: str
    signal: str
    price: Decimal
    confidence: Optional[Decimal] = None
    score: Optional[Decimal] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PickReturnRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scan_date: date
    rank: int
    symbol: str
    strategy_name: str
    signal: str
    pick_price: Decimal
    close_now: Optional[Decimal] = None
    return_to_date: Optional[float] = None
    returns: Dict[str, Optional[float]]


class ApiResponse(BaseModel):
    data: List[Dict[str, Any]]
    meta: Dict[str, Any]
