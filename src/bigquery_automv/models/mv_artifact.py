"""Data models for Smart Tuning check results and materialized view artifacts."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigquery_automv.models.query_candidate import TableReference


class MVStatus(Enum):
    """Status of a materialized view.

    Tracks the lifecycle state of a materialized view from proposal
    through creation, active usage, and eventual deprecation/removal.
    """

    PROPOSED = "proposed"  # MV recommended but not created
    CREATED = "created"  # MV exists in BigQuery
    ACTIVE = "active"  # MV is being used by Smart Tuning above threshold
    STALE = "stale"  # MV exists but usage below threshold for N days
    INVALID = "invalid"  # Base table/schema changed, MV is unusable
    DEPRECATED = "deprecated"  # MV scheduled for removal
    DROPPED = "dropped"  # MV was removed


@dataclass
class SmartTuningCheckResult:
    """Result of Smart Tuning eligibility check.

    Contains detailed analysis of why a query is or isn't eligible for
    Smart Tuning acceleration, including unsupported features and
    recommendations for MV creation.

    Attributes:
        query_hash: Hash of the query being analyzed
        eligible: True if query is eligible for Smart Tuning
        eligibility_basis: "stable" or "preview" features
        disqualification_reasons: Reasons if not eligible
        unsupported_features: List of unsupported features found
        aggregation_functions: Aggregate functions used in query
        join_types: Join types used in query
        has_ctes: True if query contains common table expressions
        subquery_types: Types of subqueries found (SCALAR, ARRAY, CORRELATED)
        cross_project_references: True if any table is in different project
        region_mismatch: True if tables don't match target dataset region
        liftable_columns: Columns in SELECT/GROUP BY for MV
        non_liftable_filters: Filters on non-output columns
        recommended_mv_filters: Shared predicates across query family
        recommended_mv_select: SELECT expressions for recommended MV
        recommended_mv_group_by: GROUP BY expressions for recommended MV
        synthesis_audit: Metadata from MV synthesis attempt
    """

    query_hash: str
    eligible: bool
    eligibility_basis: str  # "stable" or "preview"

    # Detailed analysis
    disqualification_reasons: list[str] = field(default_factory=list)
    unsupported_features: list[str] = field(default_factory=list)

    # Query structure
    aggregation_functions: list[str] = field(default_factory=list)
    join_types: list[str] = field(default_factory=list)
    has_ctes: bool = False
    subquery_types: list[str] = field(default_factory=list)

    # Cross-tenancy and region checks
    cross_project_references: bool = False
    region_mismatch: bool = False

    # Column analysis (for predicate lifting)
    liftable_columns: list[str] = field(default_factory=list)
    non_liftable_filters: list[str] = field(default_factory=list)

    # Recommendations
    recommended_mv_filters: list[str] = field(default_factory=list)
    recommended_mv_select: list[str] = field(default_factory=list)
    recommended_mv_group_by: list[str] = field(default_factory=list)

    # Synthesis audit (if MV generation was attempted)
    synthesis_audit: dict | None = None


@dataclass
class MaterializedViewArtifact:
    """A generated materialized view.

    Represents a materialized view created by the automation system,
    including its definition, configuration, metadata, and usage statistics.

    Attributes:
        mv_name: Materialized view name (e.g., automv_abc123_def456)
        source_query_hash: Links to QueryCandidate (family_hash)
        signature_hash: Stable hash of canonical MV SQL (for idempotency)
        project_id: BigQuery project ID
        dataset_id: BigQuery dataset ID
        mv_region: Explicit region (must match source tables)
        ddl_definition: Full CREATE MATERIALIZED VIEW statement
        base_tables: List of base tables referenced by MV
        refresh_interval_minutes: MV refresh interval in minutes
        enable_refresh: True if automatic refresh is enabled
        partition_expiration_days: Optional partition expiration in days
        created_at: Timestamp when MV was created
        created_by: User or service account that created the MV
        created_by_tool_version: Tool version that created this MV
        rulebook_version: Eligibility rulebook version at creation
        synthesis_version: MV synthesis algorithm version
        synthesis_warnings: Warnings from synthesis process
        status: Current status of the MV
        eligibility_basis: "stable" or "preview" features
        last_refreshed: Timestamp of last MV refresh
        usage_count: Number of times Smart Tuning used this MV
        total_bytes_saved: Total bytes saved via MV acceleration
        total_slot_ms_saved: Total slot milliseconds saved
        last_used: Timestamp of last MV usage
    """

    # Identity
    mv_name: str
    source_query_hash: str
    signature_hash: str

    # Location
    project_id: str
    dataset_id: str
    mv_region: str

    # Definition
    ddl_definition: str
    mv_query: str  # Raw SELECT query (without CREATE statement), for passing to bq_client
    base_tables: list["TableReference"]  # noqa: UP037

    # Configuration
    refresh_interval_minutes: int
    enable_refresh: bool
    partition_expiration_days: int | None

    # Metadata
    created_at: datetime
    created_by: str
    created_by_tool_version: str
    rulebook_version: str
    synthesis_version: str
    synthesis_warnings: list[str] = field(default_factory=list)

    # Status tracking
    status: MVStatus = MVStatus.PROPOSED
    eligibility_basis: str = "stable"
    last_refreshed: datetime | None = None

    # Usage statistics (from impact tracking)
    usage_count: int = 0
    total_bytes_saved: int = 0
    total_slot_ms_saved: int = 0
    last_used: datetime | None = None

    def __post_init__(self) -> None:
        """Validate materialized view artifact data.

        Raises:
            ValueError: If validation rules are violated
        """
        if not self.mv_name:
            raise ValueError("mv_name must be non-empty")

        if not self.mv_name.startswith("automv_"):
            raise ValueError("mv_name must start with 'automv_' prefix")

        if not self.source_query_hash:
            raise ValueError("source_query_hash must be non-empty")

        if not self.signature_hash:
            raise ValueError("signature_hash must be non-empty")

        if not self.base_tables:
            raise ValueError("base_tables must have at least 1 table")

        if self.refresh_interval_minutes < 1:
            raise ValueError("refresh_interval_minutes must be >= 1")
