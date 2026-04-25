"""AWS Lambda entrypoint for the serving API."""

from mangum import Mangum

from serving_api.app import app

lambda_handler = Mangum(app, lifespan="off")
