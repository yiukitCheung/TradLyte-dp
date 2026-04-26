"""AWS Lambda entrypoint for the serving API."""

import os

from mangum import Mangum

from serving_api.app import app

api_base_path = os.environ.get("API_GATEWAY_BASE_PATH", "/v1")
lambda_handler = Mangum(app, lifespan="off", api_gateway_base_path=api_base_path)
