"""
Client modules for AWS Lambda Architecture
"""

# RDSTimescaleClient is always available (psycopg2 only, no extra deps)
from .rds_timescale_client import RDSTimescaleClient, RDSPostgresClient

# PolygonClient requires 'requests' which is not installed in the lean scanner
# image — import lazily so the scanner Batch container doesn't fail on load.
def __getattr__(name):
    if name == 'PolygonClient':
        from .polygon_client import PolygonClient
        globals()['PolygonClient'] = PolygonClient
        return PolygonClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'PolygonClient',
    'RDSTimescaleClient',
    'RDSPostgresClient',
]
