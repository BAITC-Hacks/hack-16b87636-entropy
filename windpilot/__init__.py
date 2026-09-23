"""Reproducible wind-power forecasting MVP."""

import os

# Bound CPU use and avoid Windows WMIC discovery on systems where WMIC is absent.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(min(4, os.cpu_count() or 1)))

__version__ = "0.1.0"
