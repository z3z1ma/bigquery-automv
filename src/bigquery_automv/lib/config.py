"""Configuration models for bq-automv."""

from dataclasses import dataclass


@dataclass
class ImpactScoringConfig:
    """Configuration for impact scoring calculations."""

    price_per_tib: float = 6.25
    """BigQuery on-demand price per TiB in USD (default: $6.25)."""

    slot_weight: float = 0.25
    """Weight for slot component in impact score (0.0 to 1.0)."""

    slot_ms_per_tib_equivalent: float = 3.6e9
    """Slot-ms to TiB equivalent conversion factor (based on BigQuery pricing)."""

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.price_per_tib <= 0:
            raise ValueError("price_per_tib must be positive")
        if not 0.0 <= self.slot_weight <= 1.0:
            raise ValueError("slot_weight must be between 0.0 and 1.0")
        if self.slot_ms_per_tib_equivalent <= 0:
            raise ValueError("slot_ms_per_tib_equivalent must be positive")


@dataclass
class MVConfig:
    """Configuration for materialized view generation."""

    refresh_interval_minutes: int = 60
    """MV refresh interval in minutes (default: 60)."""

    enable_refresh: bool = True
    """Enable automatic refresh for materialized views."""

    mv_prefix: str = "automv_"
    """Prefix for generated materialized view names."""

    enable_auto_cleanup: bool = False
    """Enable automatic cleanup of stale materialized views."""

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.refresh_interval_minutes < 1:
            raise ValueError("refresh_interval_minutes must be at least 1")
        if self.refresh_interval_minutes > 43200:  # 30 days
            raise ValueError("refresh_interval_minutes cannot exceed 43200 (30 days)")
        if not self.mv_prefix:
            raise ValueError("mv_prefix cannot be empty")
        if not self.mv_prefix.replace("_", "").isalnum():
            raise ValueError("mv_prefix must contain only alphanumeric characters and underscores")


@dataclass
class AnalysisConfig:
    """Configuration for query analysis."""

    min_executions: int = 10
    """Minimum execution count per query family."""

    min_bytes: int = 1073741824  # 1 GB
    """Minimum bytes processed threshold (default: 1GB)."""

    min_slot_ms: int = 0
    """Minimum slot milliseconds threshold."""

    max_families: int = 100
    """Maximum number of query families to return."""

    sample_size_k: int = 20
    """Number of sample queries for MV synthesis (top K by bytes_billed)."""

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.min_executions < 1:
            raise ValueError("min_executions must be at least 1")
        if self.min_bytes < 0:
            raise ValueError("min_bytes cannot be negative")
        if self.min_slot_ms < 0:
            raise ValueError("min_slot_ms cannot be negative")
        if self.max_families < 1:
            raise ValueError("max_families must be at least 1")
        if self.sample_size_k < 1:
            raise ValueError("sample_size_k must be at least 1")
        if self.sample_size_k > 1000:
            raise ValueError("sample_size_k cannot exceed 1000")
