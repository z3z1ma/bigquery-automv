"""Data models for query candidates and table references."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Self


@dataclass(frozen=True)
class TableReference:
    """A BigQuery table referenced in a query.

    Attributes:
        project_id: BigQuery project ID
        dataset_id: BigQuery dataset ID
        table_id: Table name
        region: BigQuery region (e.g., "US", "eu")
        processed_bytes: Optional bytes processed from this specific table
    """

    project_id: str
    dataset_id: str
    table_id: str
    region: str
    processed_bytes: int | None = None

    @property
    def full_name(self) -> str:
        """Full table reference in project.dataset.table format."""
        return f"{self.project_id}.{self.dataset_id}.{self.table_id}"

    def is_cross_project(self, other_project: str) -> bool:
        """Check if this table is in a different project than the specified one.

        Args:
            other_project: Project ID to compare against

        Returns:
            True if this table is in a different project
        """
        return self.project_id != other_project

    @property
    def is_logical_view(self) -> bool:
        """Check if this is a logical view (not a base table).

        Note:
            This requires querying INFORMATION_SCHEMA.TABLES and additional
            metadata fetch. Implement when needed.

        Returns:
            True if this is a view, False if it's a base table
        """
        # TODO: Implement by querying INFORMATION_SCHEMA.TABLES
        # This requires additional metadata fetch
        return False


@dataclass
class QueryCandidate:
    """A unique query pattern identified from INFORMATION_SCHEMA.JOBS analysis.

    Represents a query family with aggregated metrics across all executions.
    Used for Smart Tuning eligibility analysis and MV recommendation.

    Attributes:
        query_hash: Normalized literals hash from BigQuery
        representative_query: Sample SQL text for this pattern
        execution_count: Number of times this query was executed
        bytes_billed_total: SUM(total_bytes_billed) across jobs in family
        total_bytes_processed: Total bytes processed across all executions
        slot_ms_total: SUM(total_slot_ms) across jobs in family
        impact_score: Dollar-equivalent impact score per Impact Scoring Model
        dollar_cost_est_on_demand: Estimated on-demand cost from bytes
        impact_model_version: Version string for scoring model (e.g., "v1.0")
        rulebook_version: Smart Tuning eligibility rulebook version
        first_seen: First time this query was observed
        last_seen: Most recent time this query was observed
        referenced_tables: List of tables referenced by this query
        statement_type: SQL statement type (SELECT, INSERT, etc.)
        has_aggregations: True if query contains aggregate functions
        aggregation_functions: List of aggregate function names used
        join_types: List of join types used (INNER, LEFT, etc.)
        has_ctes: True if query contains common table expressions
        has_unsupported_patterns: True if contains unsupported features
        smart_tuning_eligible: True if query is eligible for Smart Tuning
        eligibility_basis: "stable" or "preview" features
        smart_tuning_reasons: Disqualification reasons if not eligible
        impacted_users: List of user emails from jobs (if available)
    """

    # Identity
    query_hash: str
    representative_query: str

    # Execution metrics (aggregated across all jobs in family)
    execution_count: int
    bytes_billed_total: int
    total_bytes_processed: int
    slot_ms_total: int
    impact_score: float
    dollar_cost_est_on_demand: float

    # Scoring metadata
    impact_model_version: str
    rulebook_version: str

    # Time range
    first_seen: datetime
    last_seen: datetime

    # Query characteristics
    referenced_tables: list[TableReference] = field(default_factory=list)
    statement_type: str = "SELECT"
    has_aggregations: bool = False
    aggregation_functions: list[str] = field(default_factory=list)
    join_types: list[str] = field(default_factory=list)
    has_ctes: bool = False
    has_distinct: bool = False
    has_unsupported_patterns: bool = False

    # MV Strategy (T049, T050)
    locked_predicates: list[str] = field(default_factory=list)
    lifted_columns: list[str] = field(default_factory=list)

    # Smart Tuning eligibility
    smart_tuning_eligible: bool = False
    eligibility_basis: str = "stable"
    smart_tuning_reasons: list[str] = field(default_factory=list)

    # Users (for notification/stakeholder tracking)
    impacted_users: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate query candidate data.

        Raises:
            ValueError: If validation rules are violated
        """
        if not self.query_hash:
            raise ValueError("query_hash must be non-empty")

        if self.execution_count < 1:
            raise ValueError("execution_count must be >= 1")

        if not self.referenced_tables:
            raise ValueError("referenced_tables must have at least 1 table")

        # Derive has_aggregations from aggregation_functions
        if self.aggregation_functions and not self.has_aggregations:
            object.__setattr__(self, "has_aggregations", True)

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create QueryCandidate from dictionary (e.g. from JSON or BigQuery row).

        Handles nested structures like referenced_tables and type conversions.
        """
        import json
        from datetime import datetime

        # Handle referenced_tables
        referenced_tables = []
        tables_data = data.get("referenced_tables", [])
        if isinstance(tables_data, str):
            try:
                tables_data = json.loads(tables_data)
            except json.JSONDecodeError:
                tables_data = []

        if tables_data:
            for table_data in tables_data:
                # Handle flat dict or nested structure
                referenced_tables.append(
                    TableReference(
                        project_id=table_data.get("project_id", ""),
                        dataset_id=table_data.get("dataset_id", ""),
                        table_id=table_data.get("table_id", ""),
                        region=table_data.get("region", "US"),  # Default to US if missing
                        processed_bytes=table_data.get("processed_bytes"),
                    )
                )

        # Handle timestamps
        def parse_ts(val):
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except ValueError:
                    pass
            return datetime.now()  # Fallback

        # Handle lists that might be JSON strings or None
        def parse_list(key):
            val = data.get(key)
            if val is None:
                return []
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except json.JSONDecodeError:
                    return []
            return []

        return cls(
            query_hash=data["query_hash"],
            representative_query=data.get("representative_query", ""),
            execution_count=data.get("execution_count", 0),
            bytes_billed_total=data.get("bytes_billed_total", 0),
            total_bytes_processed=data.get("total_bytes_processed", 0),
            slot_ms_total=data.get("slot_ms_total", 0),
            impact_score=data.get("impact_score", 0.0),
            dollar_cost_est_on_demand=data.get("dollar_cost_est_on_demand", 0.0),
            impact_model_version=data.get("impact_model_version", "v1.0"),
            rulebook_version=data.get("rulebook_version", "latest"),
            first_seen=parse_ts(data.get("first_seen")),
            last_seen=parse_ts(data.get("last_seen")),
            referenced_tables=referenced_tables,
            statement_type=data.get("statement_type", "SELECT"),
            has_aggregations=data.get("has_aggregations", False),
            aggregation_functions=parse_list("aggregation_functions"),
            join_types=parse_list("join_types"),
            has_ctes=data.get("has_ctes", False),
            has_distinct=data.get("has_distinct", False),
            locked_predicates=parse_list("locked_predicates"),
            lifted_columns=parse_list("lifted_columns"),
            smart_tuning_eligible=data.get("smart_tuning_eligible", False),
            eligibility_basis=data.get("eligibility_basis", "stable"),
            smart_tuning_reasons=parse_list("smart_tuning_reasons"),
            impacted_users=parse_list("impacted_users"),
        )
