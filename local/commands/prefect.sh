# Create the work pool  
prefect work-pool create bronze-pipeline

# Deploy the deployment
prefect deploy flows/make_bronze_pipeline.py:bronze_pipeline -n bronze-pipeline-deployment -p bronze-pipeline --cron "00 16 * * 1-5" --timezone "America/New_York"
prefect deploy flows/make_silver_pipeline.py:silver_pipeline -n silver-pipeline-deployment -p silver-pipeline --cron "05 16 * * 1-5" --timezone "America/New_York"
prefect deploy flows/make_gold_pipeline.py:gold_pipeline -n gold-pipeline-deployment -p gold-pipeline --cron "10 16 * * 1-5" --timezone "America/New_York"

# Start the worker
prefect worker start -p bronze-pipeline

# Fast deploy 
prefect deploy -n gold-pipeline
