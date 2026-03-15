from kafka import KafkaConsumer 
import pandas as pd
from datetime import datetime
import sys, os, pytz, logging, time, json
from tools import kafka_client, postgres_client, utils
from prefect import get_run_logger
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  
from config.load_setting import load_setting
load_dotenv()

POSTGRES_URL = os.getenv('POSTGRES_URL')
KAFKA_BROKER = os.getenv('KAFKA_BROKER')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

class StockMetaIngestor:
    def __init__(self, settings):
        # Initialize logger first
        self.logger = get_run_logger()
        
        self.current_date = pd.to_datetime('today').strftime('%Y-%m-%d')
        self.mode = settings["mode"]
        
        # Set the topic names and table name for metadata
        self.topic_name = settings["data_extract"]["meta"]["topic_names"]
        self.table_name = settings["data_extract"]["meta"]["table_name"]
        
        # Initialize the PostgresTools client
        self.postgres_tools = postgres_client.PostgresTools(POSTGRES_URL)
        # Initialize the KafkaTools client
        self.kafka_tool = kafka_client.KafkaTools()
        
        # Initialize the Kafka consumer and subscribe to topic
        self.consumer = self._initialize_kafka_consumer()
        
        # Set batch parameters
        self.batch_size = settings["data_extract"]["meta"]["batch_size"]
        self.max_batch_time = 10  # seconds
        
    def _initialize_kafka_consumer(self):
        """Initialize Kafka consumer without subscription"""
        try:
            # Create consumer without subscription
            consumer = self.kafka_tool.create_kafka_consumer(KAFKA_BROKER)
            if not consumer:
                raise Exception("Failed to create Kafka consumer")
            self.logger.info("MetaIngestor: Successfully created Kafka consumer")
            return consumer
        except Exception as e:
            self.logger.error(f"MetaIngestor: Error initializing Kafka consumer: {e}")
            raise

    def validate_consumer(self):
        """Validate consumer connection and subscription"""
        try:
            if not self.consumer:
                return False
                
            # Check subscription
            subscription = self.consumer.subscription()
            if not subscription:
                self.logger.info("MetaIngestor: Consumer has no topic subscriptions")
                return False
                
            # Check if topic is in subscription
            if self.topic_name not in subscription:
                self.logger.info(f"MetaIngestor: Topic {self.topic_name} not in subscription")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"MetaIngestor: Error validating consumer: {e}")
            return False
    
    def should_stop_ingesting(self, last_message_time, timeout_seconds=30):
        current_time = time.time()
        time_since_last_message = current_time - last_message_time
        
        # Add periodic heartbeat logging
        if time_since_last_message > 1.0:  # Log every 1 seconds of inactivity
            self.logger.info(f"MetaIngestor: No new messages for {time_since_last_message:.1f} seconds")
        
        # Stop ingestion if no new messages for a while
        if self.mode == "catch_up" or self.mode == "reset" and time_since_last_message > timeout_seconds:
            self.logger.info(f"MetaIngestor: No new messages for {timeout_seconds} seconds. Stopping ingestion...")
            return True
            
        return False

    def subscribe_and_poll(self):
        """Subscribe to topic and start polling"""
        if not self.consumer:
            self.logger.info("MetaIngestor: No consumer available")
            return

        try:
            # Subscribe to topic
            if not self.kafka_tool.subscribe_to_topic(self.consumer, self.topic_name):
                raise Exception(f"Failed to subscribe to topic {self.topic_name}")

            batch = []
            last_message_time = time.time()
            last_batch_time = time.time()

            self.logger.info(f"MetaIngestor: Starting to poll from topic {self.topic_name}")
            
            while True:  # Use while loop for more control
                # Poll with timeout (in milliseconds)
                message_batch = self.consumer.poll(timeout_ms=1000)
                
                current_time = time.time()
                
                # Log status periodically
                if current_time - last_batch_time > 30:
                    self.logger.info(f"MetaIngestor: Still polling. Current batch size: {len(batch)}")
                    last_batch_time = current_time

                # Process received messages
                if message_batch:
                    for tp, messages in message_batch.items():
                        for message in messages:
                            try:
                                # Create a unique key for each message
                                message_value = message.value
                                message_key = message_value['symbol']  # Assuming symbol is the unique identifier
                                
                                # Only add if we haven't seen this symbol in the current batch
                                if message_key not in {msg['symbol'] for msg in batch}:
                                    batch.append(message_value)
                                    last_message_time = current_time

                                # Process batch if full or time exceeded
                                if (len(batch) >= self.batch_size) or \
                                    (current_time - last_batch_time >= self.max_batch_time):
                                    inserted = self.postgres_tools.batch_insert_meta_data(
                                        self.table_name, batch
                                    )
                                    self.logger.info(f"MetaIngestor: Inserted batch of {inserted} records")
                                    batch = []
                                    last_batch_time = current_time

                            except Exception as e:
                                self.logger.error(f"MetaIngestor: Error processing message: {e}")
                                continue

                # Check if we should stop
                if self.should_stop_ingesting(last_message_time):
                    self.logger.info("MetaIngestor: Stopping condition met")
                    break

        except Exception as e:
            self.logger.error(f"MetaIngestor: Error in subscribe_and_poll: {e}")
        finally:
            self._cleanup(batch)

    def _cleanup(self, batch):
        """Clean up resources and handle final batch"""
        try:
            if batch:
                inserted = self.postgres_tools.batch_insert_meta_data(
                    self.table_name, batch
                )
                self.logger.info(f"MetaIngestor: Final cleanup: Inserted {inserted} records")
        except Exception as e:
            self.logger.error(f"MetaIngestor: Error in final batch insert: {e}")
        finally:
            if self.consumer:
                self.consumer.close()
                self.logger.info("MetaIngestor: Consumer closed cleanly")

    def run(self):
        """Main entry point"""
        try:
            # Initialize consumer if not already done
            if not self.consumer:
                self.consumer = self._initialize_kafka_consumer()
            
            # Start subscription and polling
            self.subscribe_and_poll()
            
        except KeyboardInterrupt:
            self.logger.info("MetaIngestor: Received interrupt signal")
        except Exception as e:
            self.logger.error(f"MetaIngestor: Unexpected error in run: {e}", exc_info=True)

if __name__ == "__main__": 
    # Load the data pipeline configuration
    mode = 'development'
    settings = load_setting(mode)
    
    # Initialize and run the meta ingestor
    meta_ingestor = StockMetaIngestor(settings)
    meta_ingestor.run()