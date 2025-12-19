"""Pydantic models for the NYC Distress Signal API."""

import re
from datetime import datetime, date, timezone
from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


class Borough(str, Enum):
    """NYC Boroughs."""
    MANHATTAN = "Manhattan"
    BRONX = "Bronx"
    BROOKLYN = "Brooklyn"
    QUEENS = "Queens"
    STATEN_ISLAND = "Staten Island"


# Regex patterns for validation
HOUSE_NUMBER_PATTERN = re.compile(r"^[0-9A-Za-z\-\/\s]+$")
STREET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\s\.\'\-]+$")


class AddressRequest(BaseModel):
    """Input schema for property address lookup."""

    house_number: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="House number (e.g., '42-15', '123')",
        examples=["42-15", "123", "1A"]
    )
    street: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Street name",
        examples=["Crescent Street", "Broadway", "5th Avenue"]
    )
    borough: Borough = Field(
        ...,
        description="NYC Borough"
    )

    @field_validator("house_number")
    @classmethod
    def validate_house_number(cls, v: str) -> str:
        """Validate and normalize house number."""
        v = v.strip()
        if not v:
            raise ValueError("House number cannot be empty")

        # Check for valid characters only (prevents injection)
        if not HOUSE_NUMBER_PATTERN.match(v):
            raise ValueError(
                "House number can only contain letters, numbers, hyphens, and slashes"
            )

        # Normalize: uppercase and collapse multiple spaces
        v = " ".join(v.upper().split())
        return v

    @field_validator("street")
    @classmethod
    def validate_street(cls, v: str) -> str:
        """Validate and normalize street name."""
        v = v.strip()
        if not v:
            raise ValueError("Street name cannot be empty")

        # Check for valid characters only (prevents injection)
        if not STREET_NAME_PATTERN.match(v):
            raise ValueError(
                "Street name can only contain letters, numbers, spaces, periods, apostrophes, and hyphens"
            )

        # Normalize: collapse multiple spaces, proper case
        v = " ".join(v.split())

        # Common abbreviation expansions
        abbreviations = {
            r"\bST\b": "STREET",
            r"\bAVE\b": "AVENUE",
            r"\bAV\b": "AVENUE",
            r"\bBLVD\b": "BOULEVARD",
            r"\bRD\b": "ROAD",
            r"\bDR\b": "DRIVE",
            r"\bLN\b": "LANE",
            r"\bPL\b": "PLACE",
            r"\bCT\b": "COURT",
            r"\bPKWY\b": "PARKWAY",
        }

        v_upper = v.upper()
        for abbr, full in abbreviations.items():
            v_upper = re.sub(abbr, full, v_upper)

        return v_upper

    @property
    def formatted_address(self) -> str:
        """Return formatted address string (already normalized by validators)."""
        return f"{self.house_number} {self.street}, {self.borough.value.upper()}"

    @property
    def borough_code(self) -> int:
        """Return DOB borough code."""
        codes = {
            Borough.MANHATTAN: 1,
            Borough.BRONX: 2,
            Borough.BROOKLYN: 3,
            Borough.QUEENS: 4,
            Borough.STATEN_ISLAND: 5,
        }
        return codes[self.borough]


class DOBStatus(BaseModel):
    """Department of Buildings data extracted from BIS."""

    open_violations: int = Field(default=0, ge=0)
    stop_work_order: bool = Field(default=False)
    vacate_order: bool = Field(default=False)
    bin_number: Optional[str] = Field(default=None)
    scraped_at: datetime = Field(default_factory=_utc_now)
    error: Optional[str] = Field(default=None)


class NYC311Data(BaseModel):
    """NYC 311 complaint data."""

    total_complaints: int = Field(default=0, ge=0)
    illegal_conversion_count: int = Field(default=0, ge=0)
    heat_water_count: int = Field(default=0, ge=0)
    noise_residential_count: int = Field(default=0, ge=0)
    other_complaints: int = Field(default=0, ge=0)
    fetched_at: datetime = Field(default_factory=_utc_now)
    error: Optional[str] = Field(default=None)


class DistressSignals(BaseModel):
    """Aggregated distress signals for scoring."""

    dob_violations: int = Field(default=0)
    stop_work_order: bool = Field(default=False)
    vacate_order: bool = Field(default=False)
    complaints_311_count: int = Field(default=0)
    # HPD Violations (Housing Preservation & Development)
    hpd_class_a_count: int = Field(default=0, description="Non-hazardous violations")
    hpd_class_b_count: int = Field(default=0, description="Hazardous violations")
    hpd_class_c_count: int = Field(default=0, description="Immediately hazardous violations")


class DistressLevel(str, Enum):
    """Distress level classification."""
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AnalysisResponse(BaseModel):
    """Full analysis response for /v1/analyze endpoint."""

    address: str
    bbl: Optional[str] = Field(default=None, description="Borough-Block-Lot identifier")
    distress_score: int = Field(ge=0, le=100)
    distress_level: DistressLevel
    summary: str
    signals: DistressSignals
    partial_data: bool = Field(default=False)
    last_updated: datetime

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class AgentResponse(BaseModel):
    """Minified response for /v1/agent endpoint (LLM optimized)."""

    response: str

    @classmethod
    def from_analysis(cls, analysis: AnalysisResponse) -> "AgentResponse":
        """Create minified agent response from full analysis."""
        vacate_str = "YES" if analysis.signals.vacate_order else "NO"
        swo_str = "YES" if analysis.signals.stop_work_order else "NO"

        hpd_total = (
            analysis.signals.hpd_class_a_count +
            analysis.signals.hpd_class_b_count +
            analysis.signals.hpd_class_c_count
        )

        response = (
            f"Score: {analysis.distress_score}/100. "
            f"Signals: Vacate Order ({vacate_str}), "
            f"Stop Work Order ({swo_str}), "
            f"311 Complaints ({analysis.signals.complaints_311_count}), "
            f"DOB Violations ({analysis.signals.dob_violations}), "
            f"HPD Violations ({hpd_total}, Class C: {analysis.signals.hpd_class_c_count}). "
            f"Status: {analysis.distress_level.value}."
        )

        if analysis.partial_data:
            response += " [PARTIAL DATA]"

        return cls(response=response)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"
    version: str
    browser_ready: bool
    cache_ready: bool


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str
    detail: Optional[str] = None
    code: str


# ============== Timeline Models ==============


class EventSource(str, Enum):
    """Source of a timeline event."""
    NYC_311 = "311"
    DOB = "DOB"


class TimelineEvent(BaseModel):
    """A single event in the property timeline."""

    date: str = Field(..., description="Event date (YYYY-MM-DD)")
    source: EventSource = Field(..., description="Data source (311 or DOB)")
    event_type: str = Field(..., description="Type of event (e.g., 'Heat/Hot Water', 'Violation')")
    description: Optional[str] = Field(default=None, description="Event description/details")
    status: Optional[str] = Field(default=None, description="Status (e.g., 'Open', 'Closed')")


class MonthlySummary(BaseModel):
    """Monthly aggregation of events."""

    period: str = Field(..., description="Month period (YYYY-MM)")
    complaint_count: int = Field(default=0, ge=0, description="Number of 311 complaints")
    violation_count: int = Field(default=0, ge=0, description="Number of DOB violations/events")
    total_events: int = Field(default=0, ge=0, description="Total events this month")


class TimelineResponse(BaseModel):
    """Full timeline response for /v1/timeline endpoint."""

    address: str
    events: List[TimelineEvent] = Field(default_factory=list, description="All events sorted by date descending")
    monthly_summary: List[MonthlySummary] = Field(default_factory=list, description="Events grouped by month")
    total_events: int = Field(default=0, ge=0)
    earliest_date: Optional[str] = Field(default=None, description="Date of earliest event")
    latest_date: Optional[str] = Field(default=None, description="Date of most recent event")
    partial_data: bool = Field(default=False, description="True if some data sources failed")
    fetched_at: datetime = Field(default_factory=_utc_now)

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
