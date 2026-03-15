// create a topic
kafka-topics --create --topic topic_name --bootstrap-server localhost:9092

// delete a topic
kafka-topics --delete --topic topic_name --bootstrap-server localhost:9092

// list all topics
kafka-topics --list --bootstrap-server localhost:9092

// describe a topic
kafka-topics --describe --topic topic_name --bootstrap-server localhost:9092

// create a consumer group
kafka-consumer-groups --bootstrap-server localhost:9092 --group group_name --create

// delete a consumer group
kafka-consumer-groups --bootstrap-server localhost:9092 --group group_name --delete

// list all consumer groups
kafka-consumer-groups --bootstrap-server localhost:9092 --list

// describe a consumer group
kafka-consumer-groups --bootstrap-server localhost:9092 --group group_name --describe

// check messages in a topic
kafka-console-consumer --bootstrap-server localhost:9092 --topic topic_name --from-beginning