"""
Distress Scoring Algorithm.

Computes a normalized distress score (0-100) based on DOB, 311, and HPD data.
This is the "alpha" - the proprietary scoring logic.
"""

import logging
from datetime import datetime, timezone
from typing import Tuple, Optional

from ..config import get_settings
from ..models import (
    DOBStatus,
    NYC311Data,
    DistressSignals,
    DistressLevel,
    AnalysisResponse,
    AddressRequest,
)

logger = logging.getLogger(__name__)


# HPD Data class (avoiding circular import)
class HPDDataInput:
    """Input data from HPD client."""
    def __init__(
        self,
        class_a_count: int = 0,
        class_b_count: int = 0,
        class_c_count: int = 0,
        open_violations: int = 0,
        error: Optional[str] = None,
    ):
        self.class_a_count = class_a_count
        self.class_b_count = class_b_count
        self.class_c_count = class_c_count
        self.open_violations = open_violations
        self.error = error


class DistressScorer:
    """
    Computes property distress scores based on municipal data signals.

    Scoring Algorithm:
    - Base Score: 0
    - +50 points: Active Vacate Order (Critical Distress)
    - +40 points: Any HPD Class C violation (Immediately Hazardous)
    - +30 points: Active Stop Work Order (Financial Distress)
    - +20 points: 5+ HPD Class B violations (Hazardous)
    - +15 points: > 2 "Illegal Conversion" complaints (Regulatory Distress)
    - +10 points: 10+ HPD Class A violations (Non-hazardous)
    - +5 points: Each "Heat/Hot Water" complaint (Slumlord Signal)
    - +3 points: Each open DOB violation (Building Issues)
    - +2 points: Each noise complaint (Tenant Issues)
    - Cap: Max score 100
    """

    def __init__(self):
        self._settings = get_settings()

    def _calculate_score(
        self,
        dob_status: DOBStatus,
        nyc_311_data: NYC311Data,
        hpd_data: Optional[HPDDataInput] = None,
    ) -> int:
        """
        Calculate the raw distress score.

        Returns:
            Score between 0 and 100.
        """
        score = 0

        # Critical: Vacate Order (+50)
        if dob_status.vacate_order:
            score += self._settings.score_vacate_order
            logger.debug(f"Added {self._settings.score_vacate_order} for vacate order")

        # Critical: HPD Class C violation (+40) - Immediately Hazardous
        if hpd_data and hpd_data.class_c_count > 0:
            score += 40
            logger.debug(f"Added 40 for {hpd_data.class_c_count} HPD Class C violations")

        # High: Stop Work Order (+30)
        if dob_status.stop_work_order:
            score += self._settings.score_stop_work_order
            logger.debug(f"Added {self._settings.score_stop_work_order} for stop work order")

        # High: HPD Class B violations (+20 if 5+)
        if hpd_data and hpd_data.class_b_count >= 5:
            score += 20
            logger.debug(f"Added 20 for {hpd_data.class_b_count} HPD Class B violations")

        # Medium-High: Illegal Conversion complaints (+15 if > threshold)
        if nyc_311_data.illegal_conversion_count > self._settings.score_illegal_conversion_threshold:
            score += self._settings.score_illegal_conversion_bonus
            logger.debug(
                f"Added {self._settings.score_illegal_conversion_bonus} for "
                f"{nyc_311_data.illegal_conversion_count} illegal conversion complaints"
            )

        # Medium: HPD Class A violations (+10 if 10+)
        if hpd_data and hpd_data.class_a_count >= 10:
            score += 10
            logger.debug(f"Added 10 for {hpd_data.class_a_count} HPD Class A violations")

        # Medium: Heat/Hot Water complaints (+5 each)
        heat_water_score = (
            nyc_311_data.heat_water_count *
            self._settings.score_heat_water_per_complaint
        )
        score += heat_water_score
        if heat_water_score > 0:
            logger.debug(
                f"Added {heat_water_score} for "
                f"{nyc_311_data.heat_water_count} heat/water complaints"
            )

        # Low-Medium: DOB violations (+3 each, up to 15)
        violation_score = min(dob_status.open_violations * 3, 15)
        score += violation_score
        if violation_score > 0:
            logger.debug(
                f"Added {violation_score} for "
                f"{dob_status.open_violations} DOB violations"
            )

        # Low: Noise complaints (+2 each, up to 10)
        noise_score = min(nyc_311_data.noise_residential_count * 2, 10)
        score += noise_score
        if noise_score > 0:
            logger.debug(
                f"Added {noise_score} for "
                f"{nyc_311_data.noise_residential_count} noise complaints"
            )

        # Cap at maximum
        final_score = min(score, self._settings.score_max)

        logger.info(f"Calculated distress score: {final_score}")
        return final_score

    def _determine_level(self, score: int) -> DistressLevel:
        """
        Determine distress level based on score.

        0-25: LOW
        26-50: MODERATE
        51-75: HIGH
        76-100: CRITICAL
        """
        if score <= 25:
            return DistressLevel.LOW
        elif score <= 50:
            return DistressLevel.MODERATE
        elif score <= 75:
            return DistressLevel.HIGH
        else:
            return DistressLevel.CRITICAL

    def _generate_summary(
        self,
        score: int,
        level: DistressLevel,
        dob_status: DOBStatus,
        nyc_311_data: NYC311Data,
        hpd_data: Optional[HPDDataInput] = None,
    ) -> str:
        """Generate a human-readable summary of the analysis."""
        signals = []

        if dob_status.vacate_order:
            signals.append("Active Vacate Order")

        if hpd_data and hpd_data.class_c_count > 0:
            signals.append(f"{hpd_data.class_c_count} HPD Class C (immediately hazardous) violations")

        if dob_status.stop_work_order:
            signals.append("Active Stop Work Order")

        if hpd_data and hpd_data.class_b_count >= 5:
            signals.append(f"{hpd_data.class_b_count} HPD Class B (hazardous) violations")

        if nyc_311_data.illegal_conversion_count > self._settings.score_illegal_conversion_threshold:
            signals.append(
                f"{nyc_311_data.illegal_conversion_count} illegal conversion complaints"
            )

        if nyc_311_data.heat_water_count > 0:
            signals.append(f"{nyc_311_data.heat_water_count} heat/water complaints")

        if dob_status.open_violations > 0:
            signals.append(f"{dob_status.open_violations} open DOB violations")

        if not signals:
            return f"{level.value} RISK: No significant distress signals detected."

        signal_text = ", ".join(signals)

        if level == DistressLevel.CRITICAL:
            return f"CRITICAL RISK: {signal_text} found. Property shows severe distress indicators."
        elif level == DistressLevel.HIGH:
            return f"HIGH RISK: {signal_text} found. Property shows significant distress."
        elif level == DistressLevel.MODERATE:
            return f"MODERATE RISK: {signal_text} found. Property warrants further investigation."
        else:
            return f"LOW RISK: {signal_text} found. Minor concerns only."

    def analyze(
        self,
        address: AddressRequest,
        dob_status: DOBStatus,
        nyc_311_data: NYC311Data,
        hpd_data: Optional[HPDDataInput] = None,
        bbl: Optional[str] = None,
    ) -> AnalysisResponse:
        """
        Perform full distress analysis.

        Args:
            address: The property address
            dob_status: DOB scraper results
            nyc_311_data: 311 API results
            hpd_data: HPD violations data (optional)
            bbl: Borough-Block-Lot identifier (optional)

        Returns:
            Complete analysis response
        """
        # Calculate score
        score = self._calculate_score(dob_status, nyc_311_data, hpd_data)

        # Determine level
        level = self._determine_level(score)

        # Check for partial data
        partial_data = bool(
            dob_status.error or
            nyc_311_data.error or
            (hpd_data and hpd_data.error)
        )

        # Generate summary
        summary = self._generate_summary(score, level, dob_status, nyc_311_data, hpd_data)

        if partial_data:
            summary += " [Some data sources unavailable]"

        # Build signals object
        signals = DistressSignals(
            dob_violations=dob_status.open_violations,
            stop_work_order=dob_status.stop_work_order,
            vacate_order=dob_status.vacate_order,
            complaints_311_count=nyc_311_data.total_complaints,
            hpd_class_a_count=hpd_data.class_a_count if hpd_data else 0,
            hpd_class_b_count=hpd_data.class_b_count if hpd_data else 0,
            hpd_class_c_count=hpd_data.class_c_count if hpd_data else 0,
        )

        return AnalysisResponse(
            address=address.formatted_address,
            bbl=bbl,
            distress_score=score,
            distress_level=level,
            summary=summary,
            signals=signals,
            partial_data=partial_data,
            last_updated=datetime.now(timezone.utc),
        )


# Singleton instance
_scorer_instance = None


def get_scorer() -> DistressScorer:
    """Get the singleton scorer instance."""
    global _scorer_instance
    if _scorer_instance is None:
        _scorer_instance = DistressScorer()
    return _scorer_instance
