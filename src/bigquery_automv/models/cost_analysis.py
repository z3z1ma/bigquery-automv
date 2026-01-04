"""Data models for cost analysis of query candidates."""

from dataclasses import dataclass


@dataclass
class CostAnalysisResult:
    """Financial analysis of a query candidate.

    Provides historical cost analysis, projections, and Smart Tuning
    savings estimates for a query candidate.

    Attributes:
        query_hash: Hash of the query being analyzed
        historical_spend_usd: Actual spend over analysis period
        days_analyzed: Number of days in the analysis period
        daily_avg_spend: Average daily spend in USD
        projected_monthly_spend: Projected monthly spend in USD
        projected_yearly_spend: Projected yearly spend in USD
        smart_tuning_eligible: True if query is eligible for Smart Tuning
        estimated_savings_percentage: Estimated savings (0.0 to 1.0)
        estimated_monthly_savings_usd: Estimated monthly savings in USD
        estimated_yearly_savings_usd: Estimated yearly savings in USD
        price_per_tib: Price per TiB used for calculations (default: $6.25)
        confidence: Confidence level (HIGH, MEDIUM, LOW)
    """

    query_hash: str

    # Historical costs
    historical_spend_usd: float
    days_analyzed: int
    daily_avg_spend: float

    # Projected costs
    projected_monthly_spend: float
    projected_yearly_spend: float

    # Smart Tuning savings estimates
    smart_tuning_eligible: bool
    estimated_savings_percentage: float  # 0.0 to 1.0 (e.g., 0.8 = 80%)
    estimated_monthly_savings_usd: float
    estimated_yearly_savings_usd: float

    # Pricing model
    price_per_tib: float = 6.25

    # Confidence level
    confidence: str = "MEDIUM"  # 'HIGH', 'MEDIUM', 'LOW'

    def __post_init__(self) -> None:
        """Validate cost analysis result data.

        Raises:
            ValueError: If validation rules are violated
        """
        if self.price_per_tib <= 0:
            raise ValueError("price_per_tib must be > 0")

        if self.days_analyzed < 1:
            raise ValueError("days_analyzed must be >= 1")

        if not 0.0 <= self.estimated_savings_percentage <= 1.0:
            raise ValueError("estimated_savings_percentage must be between 0.0 and 1.0")

        if self.confidence not in ("HIGH", "MEDIUM", "LOW"):
            raise ValueError("confidence must be 'HIGH', 'MEDIUM', or 'LOW'")

        # If not eligible for Smart Tuning, savings should be zero
        if not self.smart_tuning_eligible:
            if self.estimated_savings_percentage != 0.0:
                raise ValueError("estimated_savings_percentage must be 0.0 when not eligible for Smart Tuning")
            if self.estimated_monthly_savings_usd != 0.0:
                raise ValueError("estimated_monthly_savings_usd must be 0.0 when not eligible for Smart Tuning")
            if self.estimated_yearly_savings_usd != 0.0:
                raise ValueError("estimated_yearly_savings_usd must be 0.0 when not eligible for Smart Tuning")
