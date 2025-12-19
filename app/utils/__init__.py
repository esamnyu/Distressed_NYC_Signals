"""
Shared utilities for the NYC Distress Signal API.

This module provides common functions used across multiple clients and services
to maintain DRY principles and ensure consistent behavior.
"""

from .common import (
    sanitize_soql_value,
    get_borough_name,
    get_borough_id,
    BOROUGH_NAMES,
    BOROUGH_IDS,
)

__all__ = [
    "sanitize_soql_value",
    "get_borough_name",
    "get_borough_id",
    "BOROUGH_NAMES",
    "BOROUGH_IDS",
]
