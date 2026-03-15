import polars as pl
import duckdb
import os
from dotenv import load_dotenv
import time

class DataLoader:
    
    def __init__(self, settings: dict):
        load_dotenv()
        self.db_file = settings["process"]["silver_db_path"]
        self.gold_path = settings["process"]["gold"]
        self.con = duckdb.connect(self.db_file)
        self.strategies = settings["process"]["strategies"]
        self.interval_list = settings["process"]["strategies"]["vegas_channel"]["intervals"]
        
    def get_silver_tables(self) -> list:
        """Get list of all silver tables"""
        try:
            print(', '.join(f"'{i}'" for i in [f"silver_{interval}" for interval in self.interval_list]))
            # Convert intervals to strings and join them with quotes
            tables = self.con.execute(f"""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_name IN ({', '.join(f"'{i}'" for i in [f"silver_{interval}" for interval in self.interval_list])})
                ORDER BY table_name
            """).fetchall()
            return [table[0] for table in tables]
        except Exception as e:
            print(f"Error getting silver tables: {str(e)}")
            raise
        
    def load_silver_data(self) -> pl.DataFrame:
        """Load and combine data from all silver tables"""
        start_time = time.time()
        try:
            silver_tables = self.get_silver_tables()
            if not silver_tables:
                raise ValueError("No silver tables found")
                
            print(f"Found silver tables: {silver_tables}")
            
            # Create a UNION ALL query for all silver tables
            union_queries = []
            for table in silver_tables:
                # Extract interval number from table name (e.g., 'silver_1' -> 1)
                interval = table.split('_')[1]
                union_queries.append(f"""
                    SELECT 
                        date::DATE as date,
                        symbol::VARCHAR as symbol,
                        {interval}::INTEGER as interval,
                        open::DOUBLE as open,
                        high::DOUBLE as high,
                        low::DOUBLE as low,
                        close::DOUBLE as close,
                        volume::BIGINT as volume
                    FROM {table}
                """)
            
            # Combine all queries with UNION ALL
            full_query = " UNION ALL ".join(union_queries)
            
            # Execute the combined query and convert to polars dataframe
            df = self.con.execute(full_query).pl()  

            # Sort the combined data
            df = df.sort(["symbol", "interval", "date"])
            print(f"Loaded {len(df)} rows from {len(silver_tables)} silver tables")
            # print("\nData summary:")
            # print(f"Date range: {df['date'].min()} to {df['date'].max()}")
            # print("\nUnique symbols and intervals:")
            # print(df.group_by(["symbol", "interval"]).count())
            print("Time taken: ", time.time() - start_time)
            return df
            
        except Exception as e:
            print(f"Error loading silver data: {str(e)}")
            raise
            
    def save_gold_data(self, df: pl.DataFrame, table_name: str):
        """Save processed data to gold layer"""
        try:
            # Ensure the directory exists - FIX: Use self.gold_path directly
            os.makedirs(self.gold_path, exist_ok=True)
            
            # Create the full file path
            file_path = os.path.join(self.gold_path, f'{table_name}.parquet')
            
            # Check file size only if it exists
            if os.path.exists(file_path):
                print(f"Size of file before saving: {os.path.getsize(file_path)}")
            
            # Save the pl dataframe as parquet file
            df.write_parquet(file_path)
            
            # Show the size of file after saving
            print(f"Size of file after saving: {os.path.getsize(file_path)}")
            
        except Exception as e:
            print(f"Error saving gold data: {str(e)}")
            raise
            
    def close(self):
        """Close the database connection"""
        self.con.close()
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close() 
        