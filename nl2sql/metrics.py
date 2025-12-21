"""
Deprecated shim.

All Prometheus metric definitions and the PrometheusMetrics adapter live in:
    adapters.metrics.prometheus

This module only exists for backward-compatible imports.
"""

from adapters.metrics.prometheus import *  # noqa: F401,F403
