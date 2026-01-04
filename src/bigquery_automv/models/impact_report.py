"""Data models for impact reporting of materialized view optimizations."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigquery_automv.models.mv_artifact import MVStatus


@dataclass
class MVImpact:
    """Impact statistics for a single materialized view.

    Tracks before/after performance metrics for a specific materialized view,
    including baseline comparisons and current usage statistics.

    Attributes:
        mv_name: Name of the materialized view
        source_query_hash: Hash of the source query
        baseline_avg_bytes_processed: Baseline average bytes (None if no baseline)
        baseline_avg_slot_ms: Baseline average slot milliseconds (None if no baseline)
        baseline_execution_count: Baseline execution count (None if no baseline)
        current_avg_bytes_processed: Current average bytes processed
        current_avg_slot_ms: Current average slot milliseconds
        current_execution_count: Current execution count
        bytes_saved_per_execution: Bytes saved per execution
        slot_ms_saved_per_execution: Slot milliseconds saved per execution
        total_bytes_saved: Total bytes saved across all executions
        total_slot_ms_saved: Total slot milliseconds saved across all executions
        dollars_saved_on_demand_equiv: On-demand equivalent savings in USD
        matching_executions_count: Jobs matching family hash in measurement window
        smart_tuning_usage_count: Times MV was used via Smart Tuning
        direct_query_count: Times MV was queried directly
        usage_percentage: Percentage of matching queries that used MV
        attribution_method: Method used to attribute savings
        status: Current status of the MV
    """

    mv_name: str
    source_query_hash: str

    # Baseline (before MV)
    baseline_avg_bytes_processed: int | None
    baseline_avg_slot_ms: int | None
    baseline_execution_count: int | None

    # Current (after MV)
    current_avg_bytes_processed: int
    current_avg_slot_ms: int
    current_execution_count: int

    # Savings
    bytes_saved_per_execution: int
    slot_ms_saved_per_execution: int
    total_bytes_saved: int
    total_slot_ms_saved: int
    dollars_saved_on_demand_equiv: float

    # Matching executions
    matching_executions_count: int

    # Usage
    smart_tuning_usage_count: int
    direct_query_count: int
    usage_percentage: float

    # Attribution metadata
    attribution_method: str  # "bytes_proportional", "even_split", or "unknown"

    # Status
    status: "MVStatus"  # noqa: UP037

    def __post_init__(self) -> None:
        """Validate MV impact data.

        Raises:
            ValueError: If validation rules are violated
        """
        if not 0.0 <= self.usage_percentage <= 100.0:
            raise ValueError("usage_percentage must be between 0.0 and 100.0")

        if self.current_execution_count < 0:
            raise ValueError("current_execution_count must be >= 0")

        if self.matching_executions_count < 0:
            raise ValueError("matching_executions_count must be >= 0")

        if self.attribution_method not in ("bytes_proportional", "even_split", "unknown"):
            raise ValueError("attribution_method must be 'bytes_proportional', 'even_split', or 'unknown'")


@dataclass
class ImpactReport:
    """Before/after comparison for deployed materialized views.

    Aggregates impact statistics across multiple materialized views to
    provide an overall picture of optimization effectiveness.

    Attributes:
        report_id: Unique identifier for this report
        generated_at: Timestamp when report was generated
        period_start: Start of measurement period
        period_end: End of measurement period
        baseline_mode: Type of baseline comparison used
        baseline_period_start: Start of baseline period (if applicable)
        baseline_period_end: End of baseline period (if applicable)
        mv_impacts: List of per-MV impact statistics
        total_bytes_saved: Total bytes saved across all MVs
        total_slot_ms_saved: Total slot milliseconds saved across all MVs
        total_dollars_saved_on_demand_equiv: Total on-demand equivalent savings
        total_queries_accelerated: Total number of queries accelerated
        matching_executions_count: Total matching jobs in measurement window
        unused_mvs: List of MV names with zero usage
    """

    # Report metadata
    report_id: str
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    baseline_mode: str  # "none", "previous_period", or "explicit_range"
    baseline_period_start: datetime | None
    baseline_period_end: datetime | None

    # Per-MV statistics
    mv_impacts: list[MVImpact] = field(default_factory=list)

    # Aggregates
    total_bytes_saved: int = 0
    total_slot_ms_saved: int = 0
    total_dollars_saved_on_demand_equiv: float = 0.0
    total_queries_accelerated: int = 0
    matching_executions_count: int = 0

    # Unused MVs
    unused_mvs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate impact report data.

        Raises:
            ValueError: If validation rules are violated
        """
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")

        if self.baseline_mode not in ("none", "previous_period", "explicit_range"):
            raise ValueError("baseline_mode must be 'none', 'previous_period', or 'explicit_range'")

        # If baseline mode is not 'none', baseline periods must be provided
        if self.baseline_mode != "none":
            if self.baseline_period_start is None or self.baseline_period_end is None:
                raise ValueError(
                    "baseline_period_start and baseline_period_end must be provided "
                    f"when baseline_mode is '{self.baseline_mode}'"
                )
            if self.baseline_period_end <= self.baseline_period_start:
                raise ValueError("baseline_period_end must be after baseline_period_start")
