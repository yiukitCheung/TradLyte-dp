from kafka import KafkaAdminClient, KafkaConsumer
from kafka.admin.new_topic import NewTopic
from kafka.errors import TopicAlreadyExistsError
import json, logging
from prefect import get_run_logger

# =============================================== #
# ===============  Kafka Tools  ================ #
# =============================================== #
class KafkaTools:
    def __init__(self):
        self.logger = get_run_logger()
        
    def test_connection(self, kafka_broker):
        """
        Test the connection to Kafka.
        Used in: extractor.py
        """
        try:
            admin_client = KafkaAdminClient(
                bootstrap_servers=[kafka_broker]
            )
            topics = admin_client.list_topics()
            self.logger.info(f"KafkaTools: Successfully connected to Kafka. Found topics: {topics}")
            admin_client.close()
            return True
        except Exception as e:
            self.logger.error(f"KafkaTools: Kafka connection error: {e}")
            return False

    def create_kafka_consumer(self, kafka_broker):
        """Create a Kafka consumer without topic subscription"""
        try:
            consumer = KafkaConsumer(
                bootstrap_servers=kafka_broker,
                auto_offset_reset='earliest',
                group_id='raw_group',    
                value_deserializer=lambda x: json.loads(x.decode('utf-8')),
                enable_auto_commit=True,
                auto_commit_interval_ms=5000
            )
            self.logger.info("Created Kafka consumer")
            return consumer
        except Exception as e:
            self.logger.error(f"Failed to create Kafka consumer: {e}")
            return None

    def subscribe_to_topic(self, consumer, topic_name):
        """Subscribe to a Kafka topic"""
        try:
            if consumer:
                consumer.subscribe([topic_name])
                self.logger.info(f"Subscribed to Kafka topic: {topic_name}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Failed to subscribe to topic {topic_name}: {e}")
            return False
    
    def create_kafka_topic(self, topic_names, kafka_broker):
        """
        Create a Kafka topic.
        Used in: extractor.py
        """
        
        # Test the connection
        if not self.test_connection(kafka_broker):
            self.logger.error(f"KafkaTools: Kafka connection error")
            return False
        
        # Create the topic
        try:
            admin_client = KafkaAdminClient(
                bootstrap_servers=[kafka_broker]
            )
            
            # Convert single topic to list if necessary
            if isinstance(topic_names, str):
                topic_names = [topic_names]
                
            # Create topic configurations
            topic_list = []
            for topic in topic_names:
                new_topic = NewTopic(
                    name=topic,
                    num_partitions=1,
                    replication_factor=1
                )
                topic_list.append(new_topic)
            
            # Create topics
            admin_client.create_topics(new_topics=topic_list)
            self.logger.info(f"KafkaTools: Topics {topic_names} created successfully")
            admin_client.close()
            return True
            
        except TopicAlreadyExistsError:
            self.logger.info(f"KafkaTools: Topics already exist")
            return True
        except Exception as e:
            self.logger.error(f"KafkaTools: Error creating topics: {e}")
            return False

    def delete_kafka_topics(self, topic_names, kafka_broker):
        """
        Delete a Kafka topic.
        Used in: extractor.py
        """
        try:
            admin_client = KafkaAdminClient(
                bootstrap_servers=[kafka_broker]
            )
            
            # Convert single topic to list if necessary
            if isinstance(topic_names, str):
                topic_names = [topic_names]
                
            # Get existing topics
            existing_topics = admin_client.list_topics()
            
            # Filter topics that exist
            topics_to_delete = [topic for topic in topic_names if topic in existing_topics]
            
            if not topics_to_delete:
                self.logger.info(f"KafkaTools: No topics to delete. Topics {topic_names} do not exist.")
                return True
                
            # Delete topics
            admin_client.delete_topics(topics_to_delete)
            self.logger.info(f"KafkaTools: Topics {topics_to_delete} deleted successfully")
            admin_client.close()
            return True
            
        except Exception as e:
            self.logger.error(f"KafkaTools: Error deleting topics: {e}")
            return False