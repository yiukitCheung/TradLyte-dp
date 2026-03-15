import os
import duckdb
from dotenv import load_dotenv
from prefect import get_run_logger
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)
# Remove the old sys.path.append('/opt/python') if it's causing issues locally
# sys.path.append('/opt/python') # <-- COMMENT OUT or REMOVE THIS LINE LOCALLY

from config.load_setting import load_setting # This import should now work

load_dotenv()

class Resampler:
    def __init__(self, settings):
        # Ensure storage/silver directory exists
        silver_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'storage', 'silver')
        os.makedirs(silver_dir, exist_ok=True)
        
        # Set default database file path if not provided
        self.db_file = settings['process']['silver_db_path']
        # Ensure the database file is in the silver directory
        self.db_file = os.path.join(silver_dir, os.path.basename(self.db_file))

        self.con = duckdb.connect(self.db_file)

        # Install and load the PostgreSQL extension for DuckDB
        self.con.execute("INSTALL postgres;")
        self.con.execute("LOAD postgres;")

        self.intervals = settings['process']['new_intervals']
        self.sql_path = os.path.join(project_root, settings['process']['sql_path'])

        # NEW: Get PostgreSQL source configuration
        self.pg_source = settings['process']['postgres_source']
        self.pg_conn_str = (
            f"host={self.pg_source['host']} port={self.pg_source['port']} "
            f"user={self.pg_source['user_env_var']} "      # Read directly from settings
            f"password={self.pg_source['password_env_var']} " # Read directly from settings
            f"dbname={self.pg_source['dbname']} "
            f"sslmode=disable"
        )
        self.pg_schema = self.pg_source['schema']
        self.pg_raw_ohlcv_table = self.pg_source['raw_ohlcv_table']

        if settings['mode'] == "catch_up":
            self._initialize_raw_data() # For initial load or a true catch-up
        else:
            # For 'reset' or other modes, ensure the raw_data table is created/initialized
            self.con.execute("DROP TABLE IF EXISTS raw_data;") # Ensure clean slate for reset
            self._initialize_raw_data()

        self.logger = self.get_logger()
        
    def _initialize_raw_data(self):
        """Initialize raw data table from PostgreSQL source (e.g., for full load/reset)"""
        # This will always recreate raw_data, suitable for 'reset' mode
        self.con.execute(f"""
            CREATE TABLE raw_data AS
            SELECT * FROM postgres_scan(
                '{self.pg_conn_str}',
                '{self.pg_schema}', '{self.pg_raw_ohlcv_table}'
            ); 
        """)
    
    def _insert_raw_data(self):
        """Insert new raw data incrementally from PostgreSQL source"""
        # This method is for incremental updates, assuming raw_data already exists.
        # If 'raw_data' does not exist, _initialize_raw_data should be called first.
        
        # Get the last timestamp from existing raw data in DuckDB
        last_timestamp = self.con.execute("SELECT max(timestamp) FROM raw_data").fetchone()[0]
        
        # Insert only data after the last timestamp
        self.con.execute(f"""
            INSERT INTO raw_data 
            SELECT * FROM postgres_scan(
                '{self.pg_conn_str}',
                '{self.pg_schema}', '{self.pg_raw_ohlcv_table}'
            )
            WHERE timestamp > '{last_timestamp}' -- Changed from date to timestamp for precision
        """)
    
    def get_logger(self):
        return get_run_logger()
    
    def process(self, interval):
        """Create a silver table for a specific interval"""
        import time
        start_time = time.time()
        
        self.logger.info(f"Processor: Processing silver table for interval {interval}")
        table_name = f"test_silver_{interval}"
        
        # Read the SQL template
        with open(self.sql_path, 'r') as f:
            sql_template = f.read()
        
        # Replace the interval placeholder
        sql_query = sql_template.format(interval=interval).replace("test_raw_ohlcv", "raw_data")
        
        # Check if the table exists
        exists = self.con.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=?)",
                [table_name]
                ).fetchone()[0]

        if exists:
            self.logger.info(f"Processor: Table '{table_name}' exists.")

            # Get the last timestamp of the silver table
            last_timestamp = self.con.execute(f"SELECT MAX(timestamp) FROM {table_name}").fetchone()[0] # Removed raw_data here
            self.logger.info(f"Processor: Last timestamp of {table_name} is {last_timestamp}")
            
            # Check if there's new raw data that needs to be resampled
            new_raw_data_count = self.con.execute(f"""
                SELECT COUNT(*) FROM raw_data WHERE timestamp > '{last_timestamp}'
            """).fetchone()[0]
            
            if new_raw_data_count > 0:
                self.logger.info(f"Processor: Found {new_raw_data_count} new raw records to resample")
                
                # Get the resampled data for the new period only
                incremental_query = f"""
                    SELECT * FROM ({sql_query})
                    WHERE timestamp > '{last_timestamp}'
                """
                
                # Check if the incremental query returns data
                incremental_count = self.con.execute(f"SELECT COUNT(*) FROM ({incremental_query})").fetchone()[0]
                
                if incremental_count > 0:
                    self.logger.info(f"Processor: Inserting {incremental_count} new resampled records into {table_name}")
                    # Insert new resampled data incrementally
                    self.con.execute(f"""
                        INSERT INTO {table_name}
                        {incremental_query}
                    """)
                else:
                    self.logger.info(f"Processor: No new resampled data to insert into {table_name}")
            else:
                self.logger.info(f"Processor: No new raw data to resample for {table_name}")
        
        else:
            self.logger.info(f"Processor: Table '{table_name}' does not exist.")
            self.logger.info(f"Processor: Creating table {table_name}")
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} AS 
                SELECT * FROM ({sql_query})
            """)
            # Create index
            self.con.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_symbol_timestamp ON raw_data(symbol, timestamp DESC)")
            
        end_time = time.time()
        execution_time = end_time - start_time
        self.logger.info(f"Processor: Processed silver table for interval {interval} in {execution_time:.2f} seconds")
        
    def run(self):
        """Generate all silver tables for Fibonacci intervals"""
        try:
            for interval in self.intervals:
                self.process(interval)
        finally:
            # Ensure connection is closed
            self.close()

    def close(self):
        """Close the database connection"""
        if hasattr(self, 'con') and self.con:
            self.con.close()
            self.logger.info("Resampler: Database connection closed")

    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

if __name__ == "__main__":
    settings = load_setting()
    resampler = Resampler(settings)
    resampler.run()