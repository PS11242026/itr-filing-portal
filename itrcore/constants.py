"""Assessment-year constants."""

CURRENT_AY = "2026-27"          # FY 2025-26, current filing season
AY_CHOICES = ("2026-27",)       # extend as per-AY configs are added


def fy_for_ay(ay: str) -> tuple[str, str]:
    """Financial-year date range for an assessment year, ISO dates."""
    start_year = int(ay.split("-")[0]) - 1
    return f"{start_year}-04-01", f"{start_year + 1}-03-31"
