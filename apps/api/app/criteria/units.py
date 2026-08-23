"""Explicit, code-scoped conversions for the first deterministic lab rules."""

from __future__ import annotations

from collections.abc import Mapping


class UnitCompatibilityError(ValueError):
    """Raised when a rule or fact uses a unit outside the supported code mapping."""


# Factors express one source unit in the canonical unit for its exact LOINC code.
# This deliberately small table prevents a unit label from being reused across a
# different analyte just because the text happens to look compatible.
_LAB_UNIT_FACTORS: Mapping[tuple[str, str], Mapping[str, float]] = {
    ("http://loinc.org", "2345-7"): {"mmol/L": 1.0, "mg/dL": 1 / 18.0182},
    ("http://loinc.org", "718-7"): {"g/dL": 1.0, "g/L": 0.1},
    ("http://loinc.org", "2160-0"): {
        "mg/dL": 1.0,
        "umol/L": 1 / 88.4,
        "µmol/L": 1 / 88.4,
    },
}


def validate_lab_unit(*, system: str, code: str, unit: str) -> None:
    """Require a known unit for the exact lab code before evaluation can begin."""
    supported_units = _LAB_UNIT_FACTORS.get((system, code))
    if supported_units is None or unit not in supported_units:
        raise UnitCompatibilityError(
            "The criterion lab code and unit are not in the supported conversion set."
        )


def convert_lab_value(
    *,
    system: str,
    code: str,
    value: float,
    source_unit: str,
    target_unit: str,
) -> float:
    """Convert only within the explicit conversion set for one exact laboratory test."""
    validate_lab_unit(system=system, code=code, unit=source_unit)
    validate_lab_unit(system=system, code=code, unit=target_unit)
    factors = _LAB_UNIT_FACTORS[(system, code)]
    return value * factors[source_unit] / factors[target_unit]
