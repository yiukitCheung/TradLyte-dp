import psycopg2
import pandas as pd
import logging
from psycopg2 import extras
import time
from datetime import datetime
import pandas_market_calendars as mcal
import traceback
# =============================================== #
# ===============  Postgres Tools  ============== #
# =============================================== #
class PostgresTools:
    def __init__(self, postgres_url):
        self.postgres_url = postgres_url
        self.raw_table_name = "raw"
        self.meta_table_name = "symbol_metadata"
    
        # self.engine = create_engine(postgres_url) 
        
    def fetch_latest_date(self, table_name):
        """
        Fetch the latest date from the Postgres database.
        Used in: extractor.py
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(f"SELECT symbol, MAX(timestamp) FROM {table_name} GROUP BY symbol")
            result = cursor.fetchall()
            return pd.DataFrame(result, columns=['symbol', 'timestamp'])
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching latest date by symbol: {e}")
            return pd.DataFrame(columns=['symbol', 'timestamp'])
    
    def execute_sql(self, sql):
        """
        Execute a SQL query on the Postgres database.
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(sql)
            conn.commit()
        except Exception as e:
            logging.error(f"PostgresTools: Error executing SQL: {e}")
            conn.rollback()
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()
    
    def fetch_all_tickers(self):
        """
        Fetch all tickers from the Postgres database.
        Used in: extractor.py
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(f"SELECT DISTINCT symbol FROM {self.meta_table_name}")
            tickers = cursor.fetchall()
            return [ticker[0] for ticker in tickers]
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching all tickers: {e}")
            return []
    
    def fetch_all_tablenames(self):
        """
        Fetch all table names from the Postgres database.
        Used in: extractor.py
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            table_names = cursor.fetchall()
            return [table[0] for table in table_names]
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching all table names: {e}")
            return []
    
    def fetch_all_symbol_data(self, table_name, resampled=False, pandas_df=True):
        """
        Fetch all symbol data from the Postgres database.
        Used in: databatch_processor.py
        """
        start_time = time.time()
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            if resampled:
                cursor.execute(f"SELECT * FROM {table_name} ORDER BY symbol, interval, timestamp")
            else:
                cursor.execute(f"SELECT * FROM {table_name} ORDER BY symbol, timestamp")
            result = cursor.fetchall()
            logging.info(f"PostgresTools: Fetching all symbol data took {time.time() - start_time:.2f} seconds")
            if pandas_df:
                return pd.DataFrame(result, columns=[desc[0] for desc in cursor.description])
            else:
                return result
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching all symbol data: {e}")
            return None
        
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()
    
    def fetch_latest_symbol_data(self, table_name):
        """
        Fetch all symbol data from the latest date in the Postgres database.
        Used in: databatch_processor.py
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(f"""
                WITH latest_date AS (
                    SELECT MAX(timestamp) as max_date 
                    FROM {table_name}
                )
                SELECT timestamp, symbol, open, high, low, close, volume, interval
                FROM {table_name} 
                WHERE timestamp = (SELECT max_date FROM latest_date)
            """)
            
            results = cursor.fetchall()
            columns = ['timestamp', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'interval']
            return pd.DataFrame(results, columns=columns)
            
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching latest symbol data: {e}")
            return pd.DataFrame()

        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()

    def fetch_symbol_data(self, table_name, symbol):
        """
        Fetch data for a specific symbol and convert to pandas DataFrame
        This avoids serialization issues when working with Dask
        """
        try:
            # Use psycopg2 directly instead of SQLAlchemy to avoid connection references
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            
            # Direct SQL query for better performance
            query = f"""
            SELECT timestamp, symbol, open, high, low, close, volume, interval 
            FROM {table_name}
            WHERE symbol = '{symbol}'
            ORDER BY timestamp
            """
            
            # Execute query
            cursor.execute(query)
            
            # Fetch results
            results = cursor.fetchall()
            
            # Create DataFrame from results
            columns = ['timestamp', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'interval']
            df = pd.DataFrame(results, columns=columns)
            
            # Convert date to datetime and set as index
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
            df = df.set_index('timestamp')
            
            logging.info(f"PostgresTools: Fetched {len(df)} rows for {symbol}")
            return df
            
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching data for {symbol}: {e}")
            traceback.print_exc()
            return pd.DataFrame()
            
        finally:
            # Ensure connections are closed
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()

    def fetch_symbols_chunk(self, table_name, symbols):
        """
        Fetch data for a chunk of symbols and return as a dictionary of pandas DataFrames
        """
        try:
            # Convert symbols list to a string for SQL IN clause
            symbols_str = "', '".join(symbols)
            
            # Direct SQL query for multiple symbols
            query = f"""
            SELECT timestamp, symbol, open, high, low, close, volume, interval
            FROM {table_name}
            WHERE symbol IN ('{symbols_str}')
            """
            
            # Fetch all data at once
            df = pd.read_sql(
                query, 
                self.engine, 
                index_col='timestamp', 
                parse_dates=['timestamp']
            )
            
            # Split into per-symbol DataFrames
            result = {}
            for symbol in symbols:
                symbol_data = df[df['symbol'] == symbol]
                if not symbol_data.empty:
                    result[symbol] = symbol_data
            
            logging.info(f"PostgresTools: Fetched data for {len(result)}/{len(symbols)} symbols")
            return result
            
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching data for symbol chunk: {e}")
            traceback.print_exc()
            return {}
    
    def fetch_symbol_count(self):
        """
        Fetch the number of symbols in a table
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(symbol) FROM symbol_metadata")
            return cursor.fetchone()[0]
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching symbol count: {e}")
            return 0

    def fetch_indicator_set(self):
        """
        Fetch indicator set for a specific symbol
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(f"SELECT type, parameters, description, indicator_id FROM test_indicator_definitions")
            return pd.DataFrame(cursor.fetchall(), columns=['type', 'params', 'description', 'indicator_id'])
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching indicator set: {e}")
            return []
        
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()

    def fetch_indicator_id(self, table_name, indicator_type, indicator_description):
        """
        Fetch indicator id for a specific indicator type
        """
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(f"SELECT indicator_id FROM {table_name} WHERE type = '{indicator_type}' AND parameters = '{indicator_description}'")
            return cursor.fetchone()[0]
        except Exception as e:
            logging.error(f"PostgresTools: Error fetching indicator id: {e}")
            return None
        
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()
    
    def get_trading_days(self, start_date, end_date):
        """
        Get NYSE trading days for a date range
        Moved from ResampleProcessor to make it accessible from anywhere
        """
        try:
            nyse = mcal.get_calendar('NYSE')
            schedule = nyse.schedule(start_date=start_date, end_date=end_date)
            return pd.DatetimeIndex(schedule['market_close']).tz_convert('UTC')
        except Exception as e:
            logging.error(f"PostgresTools: Error getting trading days: {e}")
            import traceback
            traceback.print_exc()
            # Return an empty DatetimeIndex as fallback
            return pd.DatetimeIndex([])

    def batch_insert_meta_data(self, table_name, batch, batch_size=100):
        """
        Insert a batch of metadata records into TimescaleDB using efficient batch insert
        
        Args:
            table_name (str): Name of the table to insert into
            records (list): List of dictionaries containing metadata
            batch_size (int): Size of batches for insertion, capped at 100
            
        Returns:
            int: Number of records successfully inserted
        """
        if not batch:
            return 0
        
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()    
            total_inserted = 0
            
            # Process in batches to avoid memory issues with very large datasets
            for i in range(0, len(batch), batch_size):
                batch = batch[i:i+batch_size]
                
                # Format values for batch insert
                values = []
                for record in batch:
                    try:
                        # Create tuple of values for metadata
                        values.append((
                            record.get('symbol'),
                            record.get('name'),
                            record.get('market'),
                            record.get('locale'),
                            record.get('active'),
                            record.get('primary_exchange'),
                            record.get('type'),
                            record.get('marketCap'),
                            record.get('sector'),
                            record.get('industry')
                        ))
                    except Exception as e:
                        logging.error(f"Error processing metadata record for batch insert: {e}, record: {record}")
                
                if not values:
                    continue
                    
                # Perform the batch insert with metadata columns
                insert_query = f"""
                                INSERT INTO {table_name} (
                                    symbol, name, market, locale, active, 
                                    primary_exchange, type, marketCap, sector, industry
                                )
                                VALUES %s
                                ON CONFLICT (symbol)
                                DO UPDATE SET
                                    symbol = EXCLUDED.symbol,
                                    name = EXCLUDED.name,
                                    market = EXCLUDED.market,
                                    locale = EXCLUDED.locale,
                                    active = EXCLUDED.active,
                                    primary_exchange = EXCLUDED.primary_exchange,
                                    type = EXCLUDED.type,
                                    marketCap = EXCLUDED.marketCap,
                                    sector = EXCLUDED.sector,
                                    industry = EXCLUDED.industry;
                                """
                
                extras.execute_values(cursor, insert_query, values)
                conn.commit()
                
                total_inserted += len(values)
                
            return total_inserted
            
        except Exception as e:
            logging.error(f"PostgresTools: Error in metadata batch insert: {e}")
            if 'conn' in locals():
                conn.rollback()
            return 0
        
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()

    def batch_insert_raw_market_data(self, table_name, records, batch_size=1000):
        """
        Insert a batch of raw market data records into TimescaleDB using efficient batch insert
        
        Args:
            table_name (str): Name of the table to insert into
            records (list): List of dictionaries containing market data
            batch_size (int): Size of batches for insertion, capped at 1000
            
        Returns:
            int: Number of records successfully inserted
        """
        if not records:
            return 0
        
        # Cap batch size at 1000 to avoid memory issues
        batch_size = min(batch_size, 1000)
        
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()    
            total_inserted = 0
            
            # Process in batches to avoid memory issues with very large datasets
            for i in range(0, len(records), batch_size):
                batch = records[i:i+batch_size]
                
                # Format values for batch insert
                values = []
                for record in batch:
                    try:
                        # Convert date string to datetime if needed
                        if isinstance(record.get('timestamp'), str):
                            timestamp = pd.to_datetime(record['timestamp'])
                        else:
                            timestamp = record['timestamp']
                            
                        # Create tuple of values
                        values.append((
                            timestamp,
                            record['symbol'],
                            record.get('open'),
                            record.get('high'),
                            record.get('low'),
                            record.get('close'),
                            record.get('volume'),
                            record.get('interval', '1d')
                        ))
                    except Exception as e:
                        logging.error(f"Error processing record for batch insert: {e}, record: {record}")
                
                if not values:
                    continue
                    
                # Perform the batch insert
                insert_query = f"""
                INSERT INTO {table_name} (timestamp, symbol, open, high, low, close, volume, interval)
                VALUES %s
                ON CONFLICT (timestamp, symbol, interval)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    interval = EXCLUDED.interval;
                """
                
                extras.execute_values(cursor, insert_query, values)
                conn.commit()
                
                total_inserted += len(values)
                
            return total_inserted
            
        except Exception as e:
            logging.error(f"PostgresTools: Error in batch insert: {e}")
            if 'conn' in locals():
                conn.rollback()
            return 0
        
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()


class JDBCSparkTools:
    def __init__(self, postgres_url, spark_engine):
        self.postgres_url = postgres_url
        self.spark_engine = spark_engine
        self._parse_postgres_url()
        
    def _parse_postgres_url(self):
        """Parse PostgreSQL URL to extract components for JDBC connection"""
        from urllib.parse import urlparse
        parsed = urlparse(self.postgres_url)
        self.host_port = parsed.netloc.split('@')[-1]
        self.database = parsed.path.lstrip('/')
        self.user = parsed.username
        self.password = parsed.password
        
    def _get_base_jdbc_properties(self, table_name=None, batch_size=1000, num_partitions=4):
        """Get base JDBC properties with customizable parameters"""
        return {
            "driver": "org.postgresql.Driver",
            "user": self.user,
            "password": self.password,
            "url": f"jdbc:postgresql://{self.host_port}/{self.database}",
            "dbtable": table_name,
            "batchsize": str(batch_size),
            "numPartitions": str(num_partitions)
        }
        
    def read_table(self, table_name, batch_size=1000, num_partitions=4, **kwargs):
        """Read entire table using JDBC"""
        properties = self._get_base_jdbc_properties(table_name, batch_size, num_partitions)
        properties.update(kwargs)
        return self.spark_engine.read \
            .format("jdbc") \
            .options(**properties) \
            .load()
            
    def read_query(self, query, batch_size=1000, num_partitions=4, **kwargs):
        """Read data using a custom SQL query"""
        properties = self._get_base_jdbc_properties(query, batch_size, num_partitions)
        properties.update(kwargs)
        return self.spark_engine.read \
            .format("jdbc") \
            .options(**properties) \
            .load()

    def read_complex_view(self, view_name, partition_column="symbol", lower_bound=None, upper_bound=None, batch_size=1000, num_partitions=4, **kwargs):
        """Read data from a complex view using JDBC with partitioning"""
        properties = self._get_base_jdbc_properties(view_name, batch_size, num_partitions)
        
        reader = self.spark_engine.read \
            .format("jdbc") \
            .options(**properties) \
            .load()
        
        # Optionally add partitioning if bounds are provided
        if lower_bound is not None and upper_bound is not None:
            reader = reader.option("partitionColumn", partition_column)\
                        .option("lowerBound", lower_bound)\
                        .option("upperBound", upper_bound)\
                        .option("numPartitions", num_partitions)
        
        return reader.load()
            
    def write_table(self, df, table_name, mode="overwrite", batch_size=1000, num_partitions=4, **kwargs):
        """Write DataFrame to table using JDBC"""
        properties = self._get_base_jdbc_properties(table_name, batch_size, num_partitions)
        properties.update(kwargs)
        df.write \
            .format("jdbc") \
            .mode(mode) \
            .options(**properties) \
            .save()
            
    def upsert_table(self, df, table_name, staging_table_name, conflict_columns, update_columns, batch_size=1000, num_partitions=4):
        """Perform upsert operation using staging table"""
        # First write to staging table
        self.write_table(df, staging_table_name, "overwrite", batch_size, num_partitions)
        
        # Then execute upsert SQL
        conflict_str = ", ".join(conflict_columns)
        update_str = ", ".join([f"{col} = EXCLUDED.{col}" for col in update_columns])
        
        upsert_sql = f"""
            INSERT INTO {table_name}
            SELECT * FROM {staging_table_name}
            ON CONFLICT ({conflict_str})
            DO UPDATE SET {update_str};
        """
        
        # Execute upsert SQL
        self.execute_sql(upsert_sql)
        
    def execute_sql(self, sql):
        """Execute SQL query directly"""
        try:
            conn = psycopg2.connect(self.postgres_url)
            cursor = conn.cursor()
            cursor.execute(sql)
            conn.commit()
        except Exception as e:
            logging.error(f"JDBCSparkTools: Error executing SQL: {e}")
            conn.rollback()
        finally:
            if 'cursor' in locals() and cursor:
                cursor.close()
            if 'conn' in locals() and conn:
                conn.close()


if __name__ == "__main__":
    postgres_url = "postgresql://yiukitcheung:409219@localhost:5432/condvest"
    postgres_tool = PostgresTools(postgres_url)
    print(postgres_tool.fetch_latest_date("raw").keys())