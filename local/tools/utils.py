import pandas as pd
import pytz
import pandas_market_calendars as mcal
from datetime import datetime
from zoneinfo import ZoneInfo
# =============================================== #
# ===============  DateTime Tools  ============== #
# =============================================== #

class DateTimeTools:
    @staticmethod
    def get_current_trading_date():
        """
        Get the most current trading date.
        """
        today = pd.to_datetime('today')
        current_date = today
        
        # Check if today is a weekend or a public holiday
        if current_date.weekday() == 5:  # Saturday
            current_date -= pd.Timedelta(days=1)
        if current_date.weekday() == 6:  # Sunday
            current_date -= pd.Timedelta(days=2)
        if current_date.weekday() == 0 and current_date.hour <= 14:  # Monday before market open
            current_date -= pd.Timedelta(days=3)
            
        return current_date

    @staticmethod
    def determine_trading_hour(interval: str):
        """
        Determine the trading hours for a given interval.
        Used in: WebSocket.py
        """
        # Get NYSE calendar
        nyse = mcal.get_calendar('NYSE')
        
        # Get current NY time
        ny_tz = pytz.timezone('America/New_York')
        today = datetime.now(ny_tz).strftime('%Y-%m-%d')
        
        # Get market schedule
        schedule = nyse.schedule(start_date=today, end_date=today)
        if schedule.empty:
            return False  # Return False if market is closed today
        
        # Check if current time is past market close
        current_time = datetime.now(pytz.timezone('America/New_York'))
        last_market_close = schedule.iloc[-1]['market_close'].tz_convert('America/New_York') 
        if current_time > last_market_close:
            return False  # Return False if after market hours
        # Get market hours
        market_open = schedule.iloc[0]['market_open'].tz_convert('America/New_York')
        market_close = schedule.iloc[0]['market_close'].tz_convert('America/New_York')
        
        # Generate hourly timestamps between market open and close
        if interval.endswith('m'):
            interval = interval.replace('m', 'min')
            
        trading_hours = pd.date_range(
            start=market_open,
            end=market_close,
            freq=interval if interval != '30m' else 'min',
            tz='America/New_York'
        )[1:].tolist()
        
        return trading_hours, market_open, market_close

    @staticmethod
    def is_trading_day():
        """
        Check if the market is open at the given server time.
        """
        today = datetime.now(ZoneInfo("America/New_York")).date()
        nyse = mcal.get_calendar('NYSE')
        schedule = nyse.schedule(start_date=today, end_date=today)
        if schedule.empty:
            return False  # Return False if market is closed today
        return True
    
    @staticmethod
    def get_days_left_to_complete_candle(latest_candle_date: datetime, interval: str):
        """
        Get the number of days left to complete a candle.
        Used in: resampler.py
        """
        # Get NYSE calendar
        nyse = mcal.get_calendar('NYSE')
        
        # Get current NY time
        ny_tz = pytz.timezone('America/New_York')
        today = datetime.now(ny_tz).strftime('%Y-%m-%d')
        
        # Convert latest_candle_date to datetime
        latest_candle_date = datetime.strptime(latest_candle_date, '%Y-%m-%d')
        
        # Get market schedule
        schedule = nyse.schedule(start_date=latest_candle_date, end_date=today)
        if schedule.empty:
            return False  # Return False if market is closed today
        
        # Get the days left to complete the candle
        days_left = int(interval) - len(schedule)
        
        return days_left
        
        