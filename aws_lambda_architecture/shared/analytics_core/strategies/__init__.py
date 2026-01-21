"""
Strategy Framework

3-Step Structure:
1. Setup (Momentum): Is the trend valid?
2. Trigger (Pattern): Did the entry happen?
3. Exit (Management): When do we sell?
"""

from .base import BaseStrategy
from .builder import CompositeStrategy

__all__ = ['BaseStrategy', 'CompositeStrategy']
