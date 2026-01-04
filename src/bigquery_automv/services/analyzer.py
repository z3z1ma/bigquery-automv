"""Analyzer service for BigQuery query pattern analysis.

This module provides the AnalyzerService for analyzing BigQuery
INFORMATION_SCHEMA.JOBS to identify expensive query patterns.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from bigquery_automv.lib.config import AnalysisConfig, ImpactScoringConfig
from bigquery_automv.lib.logging import get_logger
from bigquery_automv.models.query_candidate import QueryCandidate, TableReference
from bigquery_automv.services.bq_client import BigQueryClient
from bigquery_automv.services.smart_tuning import SmartTuningService
from bigquery_automv.services.sql_parser import SQLParseError, SQLParser


@dataclass
class AnalysisMetrics:
    """Metrics collected during analysis."""

    total_jobs_scanned: int = 0
    families_analyzed: int = 0
    skipped_with_reasons: dict[str, int] = field(default_factory=dict)
    runtime_per_phase: dict[str, float] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    """Result of query pattern analysis.

    Attributes:
        candidates: List of QueryCandidate objects
        metrics: Analysis metrics
        start_date: Start of analysis period
        end_date: End of analysis period
        project_id: Project analyzed
        region: Region analyzed
    """

    candidates: list[QueryCandidate]
    metrics: AnalysisMetrics
    start_date: datetime
    end_date: datetime
    project_id: str
    region: str


class AnalyzerService:
    """Service for analyzing BigQuery query patterns.

    This service analyzes INFORMATION_SCHEMA.JOBS to identify
    expensive query patterns that are candidates for materialized views.

    Features:
        - Pagination support for >1M jobs (T031)
        - Exclusion filters (SCRIPT, NULL bytes_billed, cache hits) (T032)
        - NULL total_bytes_billed handling with warnings (T036)
    """

    def __init__(
        self,
        client: BigQueryClient,
        *,
        smart_tuning_service: SmartTuningService | None = None,
        impact_config: ImpactScoringConfig | None = None,
        analysis_config: AnalysisConfig | None = None,
    ) -> None:
        """Initialize the analyzer service.

        Args:
            client: BigQuery client instance
            smart_tuning_service: Smart tuning eligibility checker
            impact_config: Impact scoring configuration
            analysis_config: Analysis configuration
        """
        self._client = client
        self._smart_tuning_service = smart_tuning_service
        self._impact_config = impact_config or ImpactScoringConfig()
        self._analysis_config = analysis_config or AnalysisConfig()
        self._logger = get_logger("analyzer")
        self._parser = SQLParser()

    async def analyze(
        self,
        start_date: datetime,
        end_date: datetime,
        *,
        project_id: str,
        additional_filters: list[str] | None = None,
    ) -> AnalysisResult:
        """Analyze query patterns for a time range.

        Args:
            start_date: Start of analysis window (inclusive)
            end_date: End of analysis window (inclusive)
            project_id: Project ID to analyze
            additional_filters: Additional WHERE clause filters

        Returns:
            AnalysisResult with candidates and metrics

        Raises:
            BigQueryClientError: If query fails
        """
        import time

        metrics = AnalysisMetrics()
        phase_times: dict[str, float] = {}

        # Phase 1: Scan jobs with pagination (T031)
        phase_start = time.time()
        jobs = await self._scan_jobs(
            start_date,
            end_date,
            project_id=project_id,
            additional_filters=additional_filters,
            metrics=metrics,
        )
        phase_times["scan_jobs"] = time.time() - phase_start
        metrics.total_jobs_scanned = len(jobs)

        # Phase 2: Group by normalized_literals
        phase_start = time.time()
        grouped_jobs = self._group_by_query_hash(jobs, metrics=metrics)
        phase_times["group_jobs"] = time.time() - phase_start

        # Phase 3: Apply filters
        phase_start = time.time()
        filtered_groups = self._apply_filters(grouped_jobs, metrics=metrics)
        phase_times["apply_filters"] = time.time() - phase_start

        # Phase 4: Generate candidates
        phase_start = time.time()
        candidates = await self._generate_candidates(filtered_groups)
        phase_times["generate_candidates"] = time.time() - phase_start

        # Phase 5: Sort and limit
        phase_start = time.time()
        candidates = self._sort_and_limit_candidates(candidates)
        phase_times["sort_limit"] = time.time() - phase_start

        metrics.runtime_per_phase = phase_times
        metrics.families_analyzed = len(candidates)

        self._logger.info(
            "Analysis complete",
            extra={
                "total_jobs_scanned": metrics.total_jobs_scanned,
                "families_analyzed": metrics.families_analyzed,
                "skipped_with_reasons": metrics.skipped_with_reasons,
                "runtime_per_phase": metrics.runtime_per_phase,
            },
        )

        return AnalysisResult(
            candidates=candidates,
            metrics=metrics,
            start_date=start_date,
            end_date=end_date,
            project_id=project_id,
            region=self._client.region,
        )

    async def _scan_jobs(
        self,
        start_date: datetime,
        end_date: datetime,
        *,
        project_id: str,
        additional_filters: list[str] | None = None,
        metrics: AnalysisMetrics,
    ) -> list[dict]:
        """Scan jobs from INFORMATION_SCHEMA.JOBS with pagination (T031).

        Args:
            start_date: Start of time range
            end_date: End of time range
            project_id: Project ID to filter
            additional_filters: Additional WHERE clause filters
            metrics: Metrics to update

        Returns:
            List of job records
        """
        # Build exclusion filters (T032)
        filters = additional_filters or []

        # Exclude SCRIPT statement_type
        filters.append("statement_type != 'SCRIPT'")

        # Exclude cache hits (NULL referenced_tables indicates cache hit)
        filters.append("referenced_tables IS NOT NULL")

        # Query with filters applied
        result = await self._client.query_information_schema_jobs(
            start_date=start_date,
            end_date=end_date,
            project_filter=project_id,
            additional_filters=filters,
            min_bytes_billed=None,  # Don't filter here, we'll filter later
            statement_types=["SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"],
        )

        jobs = result.rows or []

        # Count NULL total_bytes_billed queries (T036)
        # Row-level security queries have NULL bytes_billed
        null_bytes_count = sum(1 for job in jobs if job.get("total_bytes_billed") is None)
        if null_bytes_count > 0:
            metrics.skipped_with_reasons["null_bytes_billed"] = null_bytes_count
            self._logger.warning(
                f"Found {null_bytes_count} queries with NULL total_bytes_billed "
                "(likely row-level security queries) - including with warning annotation",
            )

        return jobs

    def _group_by_query_hash(
        self,
        jobs: list[dict],
        *,
        metrics: AnalysisMetrics,
    ) -> dict[str, list[dict]]:
        """Group jobs by normalized_literals hash.

        Args:
            jobs: List of job records
            metrics: Metrics to update

        Returns:
            Dictionary mapping query_hash to list of jobs
        """
        grouped: dict[str, list[dict]] = defaultdict(list)

        for job in jobs:
            query_hash = job.get("normalized_literals")
            if not query_hash:
                metrics.skipped_with_reasons["missing_query_hash"] = (
                    metrics.skipped_with_reasons.get("missing_query_hash", 0) + 1
                )
                continue

            grouped[query_hash].append(job)

        return dict(grouped)

    def _apply_filters(
        self,
        grouped_jobs: dict[str, list[dict]],
        *,
        metrics: AnalysisMetrics,
    ) -> dict[str, list[dict]]:
        """Apply analysis filters to grouped jobs.

        Args:
            grouped_jobs: Jobs grouped by query_hash
            metrics: Metrics to update

        Returns:
            Filtered dictionary of grouped jobs
        """
        filtered = {}

        for query_hash, jobs in grouped_jobs.items():
            # Aggregate metrics (handle NULL bytes_billed as 0 for aggregation)
            total_bytes_billed = sum(job.get("total_bytes_billed", 0) or 0 for job in jobs)
            total_slot_ms = sum(job.get("total_slot_ms", 0) or 0 for job in jobs)
            execution_count = len(jobs)

            # Check min_executions threshold
            if execution_count < self._analysis_config.min_executions:
                metrics.skipped_with_reasons["below_min_executions"] = (
                    metrics.skipped_with_reasons.get("below_min_executions", 0) + 1
                )
                continue

            # Check min_bytes threshold (T032: warn on NULL bytes)
            if total_bytes_billed < self._analysis_config.min_bytes:
                metrics.skipped_with_reasons["below_min_bytes"] = (
                    metrics.skipped_with_reasons.get("below_min_bytes", 0) + 1
                )
                continue

            # Check min_slot_ms threshold
            if total_slot_ms < self._analysis_config.min_slot_ms:
                metrics.skipped_with_reasons["below_min_slot_ms"] = (
                    metrics.skipped_with_reasons.get("below_min_slot_ms", 0) + 1
                )
                continue

            filtered[query_hash] = jobs

        return filtered

    async def _generate_candidates(
        self,
        grouped_jobs: dict[str, list[dict]],
    ) -> list[QueryCandidate]:
        """Generate QueryCandidate objects from grouped jobs.

        Args:
            grouped_jobs: Jobs grouped by query_hash

        Returns:
            List of QueryCandidate objects
        """
        candidates = []

        for query_hash, jobs in grouped_jobs.items():
            # Refine grouping based on partition predicates and strict locking (T049, T050)
            sub_groups = self._refine_job_group(jobs)

            for i, (locked_predicates, lifted_columns, sub_group_jobs) in enumerate(sub_groups):
                # Generate unique hash for sub-group if split occurred
                # This ensures unique identity for candidates derived from same normalized_literals
                sub_hash = f"{query_hash}_{i}" if len(sub_groups) > 1 else query_hash

                # Get representative query (most recent)
                jobs_sorted = sorted(
                    sub_group_jobs,
                    key=lambda j: j.get("creation_time", datetime.min),
                    reverse=True,
                )
                representative_job = jobs_sorted[0]
                representative_query = representative_job.get("query", "")

                # Aggregate metrics for this sub-group
                execution_count = len(sub_group_jobs)
                bytes_billed_total = sum(job.get("total_bytes_billed", 0) or 0 for job in sub_group_jobs)
                total_bytes_processed = sum(job.get("total_bytes_processed", 0) or 0 for job in sub_group_jobs)
                slot_ms_total = sum(job.get("total_slot_ms", 0) or 0 for job in sub_group_jobs)

                # Calculate impact score
                impact_score = self._calculate_impact_score(
                    bytes_billed=bytes_billed_total,
                    slot_ms=slot_ms_total,
                )

                # Calculate dollar cost
                dollar_cost_est_on_demand = self._calculate_dollar_cost(bytes_billed_total)

                # Extract referenced tables
                referenced_tables = self._extract_referenced_tables(representative_job)

                # Get time range
                first_seen = min(
                    (j.get("creation_time") for j in sub_group_jobs if j.get("creation_time")),
                    default=datetime.now(),
                )
                last_seen = max(
                    (j.get("creation_time") for j in sub_group_jobs if j.get("creation_time")),
                    default=datetime.now(),
                )

                # Extract query features from representative query
                has_aggregations = False
                has_distinct = False
                aggregation_functions: list[str] = []
                join_types: list[str] = []
                has_ctes = False

                try:
                    if representative_query:
                        ast = self._parser.parse_query(representative_query)
                        has_aggregations = self._parser.is_aggregate_query(ast)
                        has_distinct = self._parser.is_distinct_query(ast)
                        aggregation_functions = self._parser.get_aggregation_functions(ast)
                        join_types = self._parser.get_join_types(ast)
                        has_ctes = self._parser.has_ctes(ast)
                except SQLParseError as e:
                    self._logger.debug(f"Failed to parse query features for hash {sub_hash}: {e}")

                # Determine Smart Tuning eligibility
                smart_tuning_eligible = False
                eligibility_basis = "stable"
                smart_tuning_reasons: list[str] = []

                # Check for NULL bytes_billed
                has_null_bytes = any(job.get("total_bytes_billed") is None for job in sub_group_jobs)
                if has_null_bytes:
                    smart_tuning_reasons.append("Contains NULL total_bytes_billed (row-level security query)")

                # Run Smart Tuning eligibility check
                if self._smart_tuning_service and representative_query:
                    eligibility_result = await self._smart_tuning_service.check_elibility(
                        sql=representative_query,
                        query_hash=sub_hash,
                    )
                    smart_tuning_eligible = eligibility_result.eligible
                    eligibility_basis = eligibility_result.eligibility_basis
                    if not eligibility_result.eligible:
                        smart_tuning_reasons.extend(eligibility_result.disqualification_reasons)

                # Check for Identity MV (Shift Left)
                # If no locked predicates AND no aggregation AND no distinct AND no CTEs/Joins -> Identity
                if smart_tuning_eligible:
                    is_identity = (
                        not has_aggregations
                        and not has_distinct
                        and not locked_predicates
                        and not has_ctes
                        and not join_types
                    )
                    if is_identity:
                        smart_tuning_eligible = False
                        smart_tuning_reasons.append(
                            "Resulting MV would be an Identity MV (all predicates lifted, no aggregation/distinct)"
                        )

                candidate = QueryCandidate(
                    query_hash=sub_hash,
                    representative_query=representative_query,
                    execution_count=execution_count,
                    bytes_billed_total=bytes_billed_total,
                    total_bytes_processed=total_bytes_processed,
                    slot_ms_total=slot_ms_total,
                    impact_score=impact_score,
                    dollar_cost_est_on_demand=dollar_cost_est_on_demand,
                    impact_model_version="v1.0",
                    rulebook_version="v1.0",
                    first_seen=first_seen,
                    last_seen=last_seen,
                    referenced_tables=referenced_tables,
                    statement_type=representative_job.get("statement_type", "SELECT"),
                    has_aggregations=has_aggregations,
                    aggregation_functions=aggregation_functions,
                    join_types=join_types,
                    has_ctes=has_ctes,
                    has_distinct=has_distinct,
                    locked_predicates=locked_predicates,
                    lifted_columns=lifted_columns,
                    smart_tuning_eligible=smart_tuning_eligible,
                    eligibility_basis=eligibility_basis,
                    smart_tuning_reasons=smart_tuning_reasons,
                )

                candidates.append(candidate)

        return candidates

    def _refine_job_group(
        self,
        jobs: list[dict],
    ) -> list[tuple[list[str], list[str], list[dict]]]:
        """Refine a job group by sub-grouping based on partition predicates.

        Args:
            jobs: List of jobs sharing normalized_literals

        Returns:
            List of (locked_predicates, lifted_columns, jobs) tuples
        """
        # Bucket jobs by partition predicate signature
        buckets: dict[tuple[str, ...], list[dict]] = defaultdict(list)
        # Store all predicates for each job: job_id -> list[str]
        job_predicates: dict[str, list[str]] = {}

        for job in jobs:
            query = job.get("query", "")
            job_id = job.get("job_id", "")

            try:
                ast = self._parser.parse_query(query)
                predicates = self._parser.extract_where_predicates(ast)
                job_predicates[job_id] = predicates

                # Identify partition predicates
                partition_preds = []
                for pred in predicates:
                    if self._is_partition_predicate(pred):
                        partition_preds.append(pred)

                # Create signature (sorted partition predicates)
                signature = tuple(sorted(partition_preds))
                buckets[signature].append(job)

            except SQLParseError:
                # If parse fails, put in a unique fallback bucket
                buckets[("PARSE_ERROR", job_id)].append(job)
                job_predicates[job_id] = []

        sub_groups = []

        for signature, bucket_jobs in buckets.items():
            if signature and signature[0] == "PARSE_ERROR":
                # Fallback group: no locking/lifting possible
                sub_groups.append(([], [], bucket_jobs))
                continue

            # Calculate intersection of ALL predicates (Locked)
            # Start with predicates of first job
            first_job_id = bucket_jobs[0].get("job_id")
            if not first_job_id:
                continue

            common_preds_set = set(job_predicates[first_job_id])

            for job in bucket_jobs[1:]:
                job_id = job.get("job_id")
                if job_id:
                    common_preds_set.intersection_update(job_predicates[job_id])

            locked_predicates = sorted(common_preds_set)

            # Calculate Lifted Columns (from predicates NOT in locked set)
            lifted_cols_set = set()
            for job in bucket_jobs:
                job_id = job.get("job_id")
                if not job_id:
                    continue

                preds = job_predicates[job_id]
                for pred in preds:
                    if pred not in common_preds_set:
                        # This predicate differs, so we must lift its columns
                        cols = self._parser.extract_columns_from_expression(pred)
                        lifted_cols_set.update(cols)

            lifted_columns = sorted(lifted_cols_set)

            sub_groups.append((locked_predicates, lifted_columns, bucket_jobs))

        return sub_groups

    def _is_partition_predicate(self, predicate: str) -> bool:
        """Check if a predicate likely involves a partition column (heuristic).

        Args:
            predicate: SQL predicate string

        Returns:
            True if likely a partition filter
        """
        cols = self._parser.extract_columns_from_expression(predicate)
        partition_keywords = {"date", "time", "ts", "timestamp", "day", "month", "year", "dt"}

        for col in cols:
            col_lower = col.lower()
            # Check if column name contains any partition keyword
            if any(kw in col_lower for kw in partition_keywords):
                return True

            # Also check for BigQuery pseudo-columns
            if "_partition" in col_lower:
                return True

        return False

    def _calculate_impact_score(
        self,
        *,
        bytes_billed: int,
        slot_ms: int,
    ) -> float:
        """Calculate impact score using dollar-equivalent model.

        Args:
            bytes_billed: Total bytes billed
            slot_ms: Total slot milliseconds

        Returns:
            Impact score (dollar-equivalent)
        """
        # Convert bytes to TiB
        tib_billed = bytes_billed / (1024**4)

        # Calculate byte component
        byte_cost = tib_billed * self._impact_config.price_per_tib

        # Calculate slot component
        slot_tib_equiv = slot_ms / self._impact_config.slot_ms_per_tib_equivalent
        slot_cost = slot_tib_equiv * self._impact_config.price_per_tib

        # Combined score with slot weight
        impact_score = byte_cost + (slot_cost * self._impact_config.slot_weight)

        return impact_score

    def _calculate_dollar_cost(self, bytes_billed: int) -> float:
        """Calculate dollar cost from bytes billed.

        Args:
            bytes_billed: Total bytes billed

        Returns:
            Dollar cost estimate
        """
        tib_billed = bytes_billed / (1024**4)
        return tib_billed * self._impact_config.price_per_tib

    def _extract_referenced_tables(self, job: dict) -> list[TableReference]:
        """Extract referenced tables from job record.

        Args:
            job: Job record

        Returns:
            List of TableReference objects
        """
        referenced_tables = []

        tables_data = job.get("referenced_tables", [])
        if not tables_data:
            return referenced_tables

        for table in tables_data:
            try:
                table_ref = TableReference(
                    project_id=table.get("project_id", ""),
                    dataset_id=table.get("dataset_id", ""),
                    table_id=table.get("table_id", ""),
                    region=table.get("region", self._client.region),
                    processed_bytes=table.get("processed_bytes"),
                )
                referenced_tables.append(table_ref)
            except Exception:
                # Skip invalid table references
                continue

        return referenced_tables

    def _sort_and_limit_candidates(
        self,
        candidates: list[QueryCandidate],
    ) -> list[QueryCandidate]:
        """Sort candidates by impact score and apply max_families limit.

        Args:
            candidates: List of QueryCandidate objects

        Returns:
            Sorted and limited list
        """
        # Sort by impact_score descending
        sorted_candidates = sorted(
            candidates,
            key=lambda c: c.impact_score,
            reverse=True,
        )

        # Apply max_families limit
        return sorted_candidates[: self._analysis_config.max_families]

    async def get_query_samples(
        self,
        query_hash: str,
        start_date: datetime,
        end_date: datetime,
        *,
        project_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """Retrieve sample queries for a specific query hash (T026: representative query caching).

        This is useful for MV synthesis when you need the top-K queries
        by bytes_billed to analyze query patterns.

        Args:
            query_hash: Normalized literals hash to fetch samples for
            start_date: Start of analysis time window
            end_date: End of analysis time window
            project_id: Project ID to filter
            limit: Maximum number of sample queries to return (default: 20)

        Returns:
            List of dictionaries containing query samples with keys:
            - job_id: str
            - query: str
            - total_bytes_billed: int
            - total_bytes_processed: int
            - total_slot_ms: int
            - creation_time: datetime
            - user_email: str | None

        Raises:
            BigQueryClientError: If query execution fails
        """
        result = await self._client.query_information_schema_jobs(
            start_date=start_date,
            end_date=end_date,
            project_filter=project_id,
            additional_filters=[f"query_info.query_hashes.normalized_literals = '{query_hash}'"],
            statement_types=["SELECT"],
        )

        # Sort by bytes_billed descending and limit
        jobs = result.rows or []
        jobs_sorted = sorted(
            jobs,
            key=lambda j: j.get("total_bytes_billed", 0) or 0,
            reverse=True,
        )

        self._logger.info(f"Retrieved {len(jobs_sorted)} query samples for hash {query_hash}")

        return jobs_sorted[:limit]
