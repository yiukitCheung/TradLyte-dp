from kafka import KafkaConsumer 
import pandas as pd
from datetime import datetime
import sys, os, pytz, logging, time, json
from tools import kafka_client, postgres_client, utils
from prefect import get_run_logger
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  
from config.load_setting import load_setting
from pandas_market_calendars import get_calendar
import pandas_market_calendars as mcal
load_dotenv()

POSTGRES_URL = os.getenv('POSTGRES_URL')
KAFKA_BROKER = os.getenv('KAFKA_BROKER')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

class StockDataIngestor:
    def __init__(self, settings):
        # Initialize logger first
        self.logger = get_run_logger()
        
        self.current_date = pd.to_datetime('today').strftime('%Y-%m-%d')
        self.mode = settings["mode"]
        
        # Set the topic names
        self.topic_name = '1d'
        self.table_name = 'raw'
        
        # Initialize the PostgresTools client
        self.postgres_tools = postgres_client.PostgresTools(POSTGRES_URL)
        # Initialize the KafkaTools client
        self.kafka_tool = kafka_client.KafkaTools()
        
        # Initialize the Kafka consumer and subscribe to topic
        self.consumer = self._initialize_kafka_consumer()
        
        # Set batch parameters
        self.batch_size = settings["data_extract"]["raw"]["batch_size"]
        self.max_batch_time = settings["data_extract"]["raw"]["max_batch_time"]
        
        # Trade hours
        if self.mode == "production":
            # Get market schedule for today
            nyse = get_calendar('NYSE')
            today = datetime.now(pytz.timezone('America/New_York')).strftime('%Y-%m-%d')
            schedule = nyse.schedule(start_date=today, end_date=today)
            
            if not schedule.empty:
                self.market_close = schedule.iloc[0]['market_close'].tz_convert('America/New_York')
                self.logger.info(f"Ingestor: Market close time is {self.market_close}")
            else:
                self.logger.info("Ingestor: No market schedule for today")
                self.market_close = None
        else:
            self.market_close = None
        
    def _initialize_kafka_consumer(self):
        """Initialize Kafka consumer without subscription"""
        try:
            # Create consumer without subscription
            consumer = self.kafka_tool.create_kafka_consumer(KAFKA_BROKER)
            if not consumer:
                raise Exception("Failed to create Kafka consumer")
            self.logger.info("Ingestor: Successfully created Kafka consumer")
            return consumer
        except Exception as e:
            self.logger.info(f"Ingestor: Error initializing Kafka consumer: {e}")
            raise

    def validate_consumer(self):
        """Validate consumer connection and subscription"""
        try:
            if not self.consumer:
                return False
                
            # Check subscription
            subscription = self.consumer.subscription()
            if not subscription:
                self.logger.info("Ingestor: Consumer has no topic subscriptions")
                return False
                
            # Check if topic is in subscription
            if self.topic_name not in subscription:
                self.logger.info(f"Ingestor: Topic {self.topic_name} not in subscription")
                return False
                
            return True
            
        except Exception as e:
            self.logger.info(f"Ingestor: Error validating consumer: {e}")
            return False
    
    def should_stop_ingesting(self, last_message_time, timeout_seconds=15):
        current_time = time.time()
        time_since_last_message = current_time - last_message_time
        
        # Add periodic heartbeat logging
        if (time_since_last_message > 1.0):  # Log every 1 seconds of inactivity
            self.logger.info(f"Ingestor: No new messages for {time_since_last_message:.1f} seconds")
        
        # Stop ingestion if no new messages for a while
        if (self.mode in ["catch_up", "reset"]) and (time_since_last_message > timeout_seconds):
            self.logger.info(f"Ingestor: No new messages for {timeout_seconds} seconds. Stopping ingestion...")
            return True
        
        if (self.market_close != None) and (self.mode == "production"):
            current_time = datetime.now(pytz.timezone('America/New_York'))
            if current_time > self.market_close + pd.Timedelta(minutes=3):
                self.logger.info(f"Ingestor: Market closed at {self.market_close.time()}, current time is {current_time.time()}. Stopping ingestion...")
                return True
            
        return False

    def subscribe_and_poll(self):
        """Subscribe to topic and start polling"""
        if not self.consumer:
            self.logger.info("Ingestor: No consumer available")
            return

        try:
            # Subscribe to topic
            if not self.kafka_tool.subscribe_to_topic(self.consumer, self.topic_name):
                raise Exception(f"Failed to subscribe to topic {self.topic_name}")

            batch = []
            last_message_time = time.time()
            last_batch_time = time.time()

            self.logger.info(f"Ingestor: Starting to poll from topic {self.topic_name}")
            
            while True:  # Use while loop for more control
                # Poll with timeout (in milliseconds)
                message_batch = self.consumer.poll(timeout_ms=1000)
                
                current_time = time.time()
                
                # Log status periodically
                if current_time - last_batch_time > self.max_batch_time:
                    self.logger.info(f"Ingestor: Still polling. Current batch size: {len(batch)}")
                    last_batch_time = current_time

                # Process received messages
                if message_batch:
                    for tp, messages in message_batch.items():
                        for message in messages:
                            try:
                                batch.append(message.value)
                                last_message_time = current_time

                                # Process batch if full or time exceeded
                                if (len(batch) >= self.batch_size) or \
                                    (current_time - last_batch_time >= self.max_batch_time):
                                    inserted = self.postgres_tools.batch_insert_raw_market_data(
                                        self.table_name, batch
                                    )
                                    self.logger.info(f"Ingestor: Inserted batch of {inserted} records")
                                    batch = []
                                    last_batch_time = current_time

                            except Exception as e:
                                self.logger.info(f"Ingestor: Error processing message: {e}")
                                continue

                # Check if we should stop
                if self.should_stop_ingesting(last_message_time):
                    self.logger.info("Ingestor: Stopping condition met")
                    break

        except Exception as e:
            self.logger.info(f"Ingestor: Error in subscribe_and_poll: {e}")
        finally:
            self._cleanup(batch)

    def _cleanup(self, batch):
        """Clean up resources and handle final batch"""
        try:
            if batch:
                inserted = self.postgres_tools.batch_insert_raw_market_data(
                    self.table_name, batch
                )
                self.logger.info(f"Ingestor: Final cleanup: Inserted {inserted} records")
        except Exception as e:
            self.logger.info(f"Ingestor: Error in final batch insert: {e}")
        finally:
            if self.consumer:
                self.consumer.close()
                self.logger.info("Ingestor: Consumer closed cleanly")

    def run(self):
        """Main entry point"""
        try:
            # Initialize consumer if not already done
            if not self.consumer:
                self.consumer = self._initialize_kafka_consumer()
            
            # Start subscription and polling
            self.subscribe_and_poll()
            
        except KeyboardInterrupt:
            self.logger.info("Ingestor: Received interrupt signal")
        except Exception as e:
            self.logger.info(f"Ingestor: Unexpected error in run: {e}", exc_info=True)

if __name__ == "__main__": 
    # Load the data pipeline configuration
    mode = 'development'
    settings = load_setting(mode)
    
    # Initialize and run the ingestor
    ingestor = StockDataIngestor(settings)
    ingestor.run()