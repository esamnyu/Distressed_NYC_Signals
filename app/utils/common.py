"""
Common utilities shared across clients and services.

Provides centralized implementations for:
- SoQL query sanitization (injection prevention)
- Borough name/ID mappings
"""

import re
from typing import Optional

from ..models import Borough


# Centralized borough mappings - single source of truth
BOROUGH_NAMES = {
    Borough.MANHATTAN: "Manhattan",
    Borough.BRONX: "Bronx",
    Borough.BROOKLYN: "Brooklyn",
    Borough.QUEENS: "Queens",
    Borough.STATEN_ISLAND: "Staten Island",
}

BOROUGH_IDS = {
    Borough.MANHATTAN: "1",
    Borough.BRONX: "2",
    Borough.BROOKLYN: "3",
    Borough.QUEENS: "4",
    Borough.STATEN_ISLAND: "5",
}

# Reverse mappings for lookups
BOROUGH_NAME_TO_ENUM = {v.upper(): k for k, v in BOROUGH_NAMES.items()}
BOROUGH_ID_TO_ENUM = {v: k for k, v in BOROUGH_IDS.items()}


def sanitize_soql_value(value: str, max_length: int = 200) -> str:
    """
    Sanitize a value for use in SoQL (Socrata Query Language) queries.

    Prevents SoQL injection attacks by:
    - Limiting input length to prevent buffer overflow
    - Escaping single quotes (primary injection vector)
    - Removing dangerous special characters

    Args:
        value: The string value to sanitize
        max_length: Maximum allowed length (default: 200)

    Returns:
        Sanitized string safe for SoQL query inclusion

    Example:
        >>> sanitize_soql_value("O'Brien's Store")
        "O''Brien''s Store"
        >>> sanitize_soql_value("Test; DROP TABLE--")
        "Test DROP TABLE"
    """
    if not value:
        return ""

    # Limit length to prevent buffer issues
    value = value[:max_length]

    # Escape single quotes by doubling them (SoQL standard)
    value = value.replace("'", "''")

    # Remove potentially dangerous characters for query injection
    # Semicolons, double dashes (SQL comments), pipes, ampersands,
    # dollar signs, and brackets
    value = re.sub(r'[;\-\-\|\&\$\(\)\[\]\{\}]', '', value)

    return value.strip()


def get_borough_name(borough: Borough, format: str = "title") -> str:
    """
    Convert Borough enum to display name.

    Args:
        borough: Borough enum value
        format: Output format - "title" (default), "upper", or "lower"

    Returns:
        Borough name string in requested format

    Example:
        >>> get_borough_name(Borough.MANHATTAN)
        "Manhattan"
        >>> get_borough_name(Borough.STATEN_ISLAND, format="upper")
        "STATEN ISLAND"
    """
    name = BOROUGH_NAMES.get(borough, "Unknown")

    if format == "upper":
        return name.upper()
    elif format == "lower":
        return name.lower()
    return name


def get_borough_id(borough: Borough) -> str:
    """
    Convert Borough enum to NYC borough ID (1-5).

    Args:
        borough: Borough enum value

    Returns:
        Borough ID as string ("1" through "5")

    Example:
        >>> get_borough_id(Borough.MANHATTAN)
        "1"
        >>> get_borough_id(Borough.BROOKLYN)
        "3"
    """
    return BOROUGH_IDS.get(borough, "0")


def get_borough_from_id(borough_id: str) -> Optional[Borough]:
    """
    Convert NYC borough ID to Borough enum.

    Args:
        borough_id: Borough ID string ("1" through "5")

    Returns:
        Borough enum or None if invalid ID

    Example:
        >>> get_borough_from_id("1")
        Borough.MANHATTAN
    """
    return BOROUGH_ID_TO_ENUM.get(borough_id)


def get_borough_from_name(name: str) -> Optional[Borough]:
    """
    Convert borough name string to Borough enum.

    Args:
        name: Borough name (case-insensitive)

    Returns:
        Borough enum or None if invalid name

    Example:
        >>> get_borough_from_name("brooklyn")
        Borough.BROOKLYN
    """
    return BOROUGH_NAME_TO_ENUM.get(name.upper())
