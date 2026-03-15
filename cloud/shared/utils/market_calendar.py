"""
Market calendar utilities for determining trading days
"""

import pandas as pd
import pandas_market_calendars as mcal
from datetime import datetime, date, timedelta
from typing import bool
import logging

logger = logging.getLogger(__name__)

# Cache for market calendar
_nyse_calendar = None


def get_nyse_calendar():
    """Get NYSE market calendar with caching"""
    global _nyse_calendar
    if _nyse_calendar is None:
        _nyse_calendar = mcal.get_calendar('NYSE')
    return _nyse_calendar


def is_trading_day(check_date: date) -> bool:
    """
    Check if a given date is a trading day
    
    Args:
        check_date: Date to check
        
    Returns:
        bool: True if it's a trading day
    """
    try:
        nyse = get_nyse_calendar()
        
        # Get trading days for the date range
        trading_days = nyse.valid_days(
            start_date=check_date,
            end_date=check_date
        )
        
        # Check if our date is in the trading days
        return len(trading_days) > 0 and check_date in trading_days.date
        
    except Exception as e:
        logger.error(f"Error checking trading day for {check_date}: {str(e)}")
        
        # Fallback: basic weekday check (Monday=0, Sunday=6)
        return check_date.weekday() < 5  # Monday-Friday


def get_previous_trading_day(from_date: date = None) -> date:
    """
    Get the previous trading day from a given date
    
    Args:
        from_date: Date to start from (defaults to today)
        
    Returns:
        date: Previous trading day
    """
    if from_date is None:
        from_date = datetime.now().date()
    
    try:
        nyse = get_nyse_calendar()
        
        # Look back up to 10 days to find a trading day
        end_date = from_date - timedelta(days=1)
        start_date = from_date - timedelta(days=10)
        
        trading_days = nyse.valid_days(
            start_date=start_date,
            end_date=end_date
        )
        
        if len(trading_days) > 0:
            return trading_days[-1].date()  # Most recent trading day
        
    except Exception as e:
        logger.error(f"Error getting previous trading day: {str(e)}")
    
    # Fallback: simple weekday logic
    check_date = from_date - timedelta(days=1)
    days_back = 1
    
    while days_back <= 7:  # Don't go back more than a week
        if check_date.weekday() < 5:  # Monday-Friday
            return check_date
        check_date = from_date - timedelta(days=days_back + 1)
        days_back += 1
    
    # Ultimate fallback
    return from_date - timedelta(days=1)


def get_next_trading_day(from_date: date = None) -> date:
    """
    Get the next trading day from a given date
    
    Args:
        from_date: Date to start from (defaults to today)
        
    Returns:
        date: Next trading day
    """
    if from_date is None:
        from_date = datetime.now().date()
    
    try:
        nyse = get_nyse_calendar()
        
        # Look ahead up to 10 days to find a trading day
        start_date = from_date + timedelta(days=1)
        end_date = from_date + timedelta(days=10)
        
        trading_days = nyse.valid_days(
            start_date=start_date,
            end_date=end_date
        )
        
        if len(trading_days) > 0:
            return trading_days[0].date()  # Next trading day
        
    except Exception as e:
        logger.error(f"Error getting next trading day: {str(e)}")
    
    # Fallback: simple weekday logic
    check_date = from_date + timedelta(days=1)
    days_ahead = 1
    
    while days_ahead <= 7:  # Don't go ahead more than a week
        if check_date.weekday() < 5:  # Monday-Friday
            return check_date
        check_date = from_date + timedelta(days=days_ahead + 1)
        days_ahead += 1
    
    # Ultimate fallback
    return from_date + timedelta(days=1)


def get_trading_days_between(start_date: date, end_date: date) -> list:
    """
    Get all trading days between two dates (inclusive)
    
    Args:
        start_date: Start date
        end_date: End date
        
    Returns:
        list: List of trading days
    """
    try:
        nyse = get_nyse_calendar()
        
        trading_days = nyse.valid_days(
            start_date=start_date,
            end_date=end_date
        )
        
        return [day.date() for day in trading_days]
        
    except Exception as e:
        logger.error(f"Error getting trading days between {start_date} and {end_date}: {str(e)}")
        
        # Fallback: simple weekday logic
        trading_days = []
        current_date = start_date
        
        while current_date <= end_date:
            if current_date.weekday() < 5:  # Monday-Friday
                trading_days.append(current_date)
            current_date += timedelta(days=1)
        
        return trading_days


def is_market_open() -> bool:
    """
    Check if the market is currently open
    
    Returns:
        bool: True if market is open
    """
    try:
        nyse = get_nyse_calendar()
        now = pd.Timestamp.now(tz='America/New_York')
        
        # Check if today is a trading day
        if not is_trading_day(now.date()):
            return False
        
        # Get market open/close times for today
        schedule = nyse.schedule(start_date=now.date(), end_date=now.date())
        
        if len(schedule) > 0:
            market_open = schedule.iloc[0]['market_open']
            market_close = schedule.iloc[0]['market_close']
            
            return market_open <= now <= market_close
        
        return False
        
    except Exception as e:
        logger.error(f"Error checking if market is open: {str(e)}")
        
        # Fallback: basic time check (9:30 AM - 4:00 PM ET on weekdays)
        now = datetime.now()
        
        # Basic weekday check
        if now.weekday() >= 5:  # Weekend
            return False
        
        # Basic time check (assuming local time is ET)
        market_open_hour = 9
        market_open_minute = 30
        market_close_hour = 16
        market_close_minute = 0
        
        current_minutes = now.hour * 60 + now.minute
        market_open_minutes = market_open_hour * 60 + market_open_minute
        market_close_minutes = market_close_hour * 60 + market_close_minute
        
        return market_open_minutes <= current_minutes <= market_close_minutes


def get_market_schedule(date_range: int = 30) -> pd.DataFrame:
    """
    Get market schedule for a date range
    
    Args:
        date_range: Number of days to get schedule for
        
    Returns:
        pd.DataFrame: Market schedule
    """
    try:
        nyse = get_nyse_calendar()
        
        start_date = datetime.now().date()
        end_date = start_date + timedelta(days=date_range)
        
        schedule = nyse.schedule(start_date=start_date, end_date=end_date)
        return schedule
        
    except Exception as e:
        logger.error(f"Error getting market schedule: {str(e)}")
        return pd.DataFrame()


# Market hours constants
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MINUTE = 0

# Pre-market and after-hours
PRE_MARKET_OPEN_HOUR = 4
PRE_MARKET_OPEN_MINUTE = 0
AFTER_HOURS_CLOSE_HOUR = 20
AFTER_HOURS_CLOSE_MINUTE = 0
