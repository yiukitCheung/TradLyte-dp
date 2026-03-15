"""
Setup script to make analytics_core pip-installable
"""

from setuptools import setup, find_packages

setup(
    name="analytics-core",
    version="0.1.0",
    description="TradLyte Analytics Engine - The Brain",
    author="TradLyte Team",
    packages=find_packages(),
    install_requires=[
        "polars>=0.19.0",
        "pydantic>=2.0.0",
        "boto3>=1.28.0",
        "psycopg2-binary>=2.9.0",
        "sqlalchemy>=2.0.0",
    ],
    python_requires=">=3.9",
)
