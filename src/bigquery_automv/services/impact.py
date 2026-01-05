"""Impact service for materialized view usage and savings analysis.

This module provides the ImpactService for analyzing materialized view
effectiveness through BigQuery INFORMATION_SCHEMA.JOBS materialized_view_statistics.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bigquery_automv.lib.config import ImpactScoringConfig
from bigquery_automv.lib.logging import get_logger
from bigquery_automv.models.impact_report import ImpactReport, MVImpact
from bigquery_automv.models.mv_artifact import MVStatus
from bigquery_automv.services.bq_client import BigQueryClient


@dataclass
class PeriodMetrics:
    """Metrics for a time period.

    Attributes:
        total_bytes_processed: Total bytes processed
        total_slot_ms: Total slot milliseconds
        execution_count: Number of executions
        avg_bytes_processed: Average bytes per execution
        avg_slot_ms: Average slot milliseconds per execution
    """

    total_bytes_processed: int = 0
    total_slot_ms: int = 0
    execution_count: int = 0
    avg_bytes_processed: float = 0.0
    avg_slot_ms: float = 0.0

    def __post_init__(self) -> None:
        """Calculate averages."""
        if self.execution_count > 0:
            self.avg_bytes_processed = self.total_bytes_processed / self.execution_count
            self.avg_slot_ms = self.total_slot_ms / self.execution_count


class ImpactService:
    """Service for analyzing materialized view impact and savings.

    This service analyzes BigQuery INFORMATION_SCHEMA.JOBS to track
    materialized view usage via Smart Tuning and calculate cost savings.

    Features:
        - MV usage detection via materialized_view_statistics (T098)
        - Matching executions query (T099)
        - Baseline mode support (T100)
        - Savings calculation (T101)
        - Per-MV attribution (T102)
        - Unused MV detection (T103)
    """

    def __init__(
        self,
        client: BigQueryClient,
        *,
        impact_config: ImpactScoringConfig | None = None,
    ) -> None:
        """Initialize the impact service.

        Args:
            client: BigQuery client instance
            impact_config: Impact scoring configuration
        """
        self._client = client
        self._impact_config = impact_config or ImpactScoringConfig()
        self._logger = get_logger("impact")

    async def get_mv_usage_stats(
        self,
        mv_name: str,
        days: int = 7,
    ) -> dict:
        """Get usage statistics for a specific materialized view.

        Args:
            mv_name: Materialized view name (e.g., "dataset.mv_name" or "project.dataset.mv_name")
            days: Number of days to look back (default: 7)

        Returns:
            Dictionary with usage statistics:
                - mv_name: str
                - period_days: int
                - query_count: int
                - total_slot_ms: int
                - total_bytes_processed: int
                - first_used: datetime | None
                - last_used: datetime | None

        Raises:
            BigQueryClientError: If query fails
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        self._logger.info(
            "Getting MV usage stats",
            extra={
                "mv_name": mv_name,
                "days": days,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )

        # Extract table_id from mv_name
        if mv_name.count(".") == 2:
            # project.dataset.table
            _, _, table_id = mv_name.split(".")
        elif mv_name.count(".") == 1:
            # dataset.table
            _, table_id = mv_name.split(".")
        else:
            # table only
            table_id = mv_name

        result = await self._client.query_materialized_view_statistics(
            start_date=start_date,
            end_date=end_date,
            mv_name=table_id,
            chosen_only=False,  # Get all MV statistics, not just chosen
        )

        rows = result.rows or []

        # Calculate statistics
        query_count = len(rows)
        total_slot_ms = sum(row.get("total_slot_ms", 0) or 0 for row in rows)
        total_bytes_processed = sum(row.get("total_bytes_processed", 0) or 0 for row in rows)

        # Find first and last usage
        first_used = None
        last_used = None

        if rows:
            # Sort by creation_time
            sorted_rows = sorted(
                rows,
                key=lambda r: r.get("creation_time") or datetime.min,
            )
            first_used = sorted_rows[0].get("creation_time")
            last_used = sorted_rows[-1].get("creation_time")

        return {
            "mv_name": mv_name,
            "period_days": days,
            "query_count": query_count,
            "total_slot_ms": total_slot_ms,
            "total_bytes_processed": total_bytes_processed,
            "first_used": first_used.isoformat() if first_used else None,
            "last_used": last_used.isoformat() if last_used else None,
        }

    async def generate_impact_report(
        self,
        start_date: date,
        end_date: date,
        *,
        baseline_mode: str = "none",
        baseline_start: date | None = None,
        baseline_end: date | None = None,
        mv_name_filter: str | None = None,
    ) -> ImpactReport:
        """Generate impact report for materialized views.

        Args:
            start_date: Start of measurement period
            end_date: End of measurement period
            baseline_mode: Baseline comparison mode (none, previous_period, explicit_range)
            baseline_start: Start of baseline period (for explicit_range mode)
            baseline_end: End of baseline period (for explicit_range mode)
            mv_name_filter: Optional filter for specific MV name

        Returns:
            ImpactReport with usage and savings statistics

        Raises:
            ValueError: If parameters are invalid
            BigQueryClientError: If query fails
        """
        report_id = str(uuid.uuid4())
        generated_at = datetime.now()

        # Convert dates to datetime
        period_start = datetime.combine(start_date, datetime.min.time())
        period_end = datetime.combine(end_date, datetime.max.time())

        # Determine baseline periods
        baseline_period_start: datetime | None = None
        baseline_period_end: datetime | None = None

        if baseline_mode == "previous_period":
            # Calculate previous period of same length
            days_diff = (end_date - start_date).days
            baseline_end_calc = start_date - timedelta(days=1)
            baseline_start_calc = baseline_end_calc - timedelta(days=days_diff)
            baseline_period_start = datetime.combine(baseline_start_calc, datetime.min.time())
            baseline_period_end = datetime.combine(baseline_end_calc, datetime.max.time())
        elif baseline_mode == "explicit_range":
            if not baseline_start or not baseline_end:
                msg = "baseline_start and baseline_end are required when baseline_mode is 'explicit_range'"
                raise ValueError(msg)
            baseline_period_start = datetime.combine(baseline_start, datetime.min.time())
            baseline_period_end = datetime.combine(baseline_end, datetime.max.time())

        self._logger.info(
            "Generating impact report",
            extra={
                "report_id": report_id,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "baseline_mode": baseline_mode,
                "mv_name_filter": mv_name_filter,
            },
        )

        # T098: Query MV usage statistics
        mv_usage = await self._query_mv_usage(
            period_start,
            period_end,
            mv_name_filter=mv_name_filter,
        )

        # Get list of all MVs from metadata (or filter by mv_name_filter)
        all_mvs = await self._get_all_materialized_views(mv_name_filter)

        # Group usage by MV
        mv_usage_by_name = self._group_usage_by_mv(mv_usage)

        # Query baseline statistics if needed
        baseline_metrics_by_mv: dict[str, PeriodMetrics] = {}
        if baseline_mode != "none" and baseline_period_start and baseline_period_end:
            baseline_metrics_by_mv = await self._query_baseline_metrics(
                baseline_period_start,
                baseline_period_end,
                list(mv_usage_by_name.keys()),
                mv_name_filter,
            )

        # Calculate impact for each MV
        mv_impacts: list[MVImpact] = []
        for mv_name, usage_jobs in mv_usage_by_name.items():
            # Get MV metadata
            mv_metadata = await self._get_mv_metadata(mv_name)

            # Calculate current period metrics
            current_metrics = self._calculate_period_metrics(usage_jobs)

            # Get baseline metrics
            baseline_metrics = baseline_metrics_by_mv.get(mv_name)

            # Calculate matching executions
            matching_executions = await self._query_matching_executions(
                period_start,
                period_end,
                mv_metadata.get("source_query_hash") if mv_metadata else None,
            )

            # Calculate direct MV usage
            direct_usage = await self._query_direct_mv_usage(
                period_start,
                period_end,
                mv_name,
            )

            # T101: Calculate savings
            savings = self._calculate_savings(
                current_metrics=current_metrics,
                baseline_metrics=baseline_metrics,
                smart_tuning_usage_count=len(usage_jobs),
                matching_executions_count=matching_executions,
            )

            # T102: Calculate attribution
            attribution_method = self._determine_attribution_method(
                len(mv_usage_by_name),
                len(usage_jobs),
            )

            # Create MVImpact
            mv_impact = MVImpact(
                mv_name=mv_name,
                source_query_hash=mv_metadata.get("source_query_hash", "unknown") if mv_metadata else "unknown",
                baseline_avg_bytes_processed=baseline_metrics.avg_bytes_processed if baseline_metrics else None,
                baseline_avg_slot_ms=baseline_metrics.avg_slot_ms if baseline_metrics else None,
                baseline_execution_count=baseline_metrics.execution_count if baseline_metrics else None,
                current_avg_bytes_processed=int(current_metrics.avg_bytes_processed),
                current_avg_slot_ms=int(current_metrics.avg_slot_ms),
                current_execution_count=current_metrics.execution_count,
                bytes_saved_per_execution=savings["bytes_saved_per_execution"],
                slot_ms_saved_per_execution=savings["slot_ms_saved_per_execution"],
                total_bytes_saved=savings["total_bytes_saved"],
                total_slot_ms_saved=savings["total_slot_ms_saved"],
                dollars_saved_on_demand_equiv=savings["dollars_saved"],
                matching_executions_count=matching_executions,
                smart_tuning_usage_count=len(usage_jobs),
                direct_query_count=direct_usage,
                usage_percentage=savings["usage_percentage"],
                attribution_method=attribution_method,
                status=self._parse_mv_status(mv_metadata.get("status") if mv_metadata else "proposed"),
            )

            mv_impacts.append(mv_impact)

        # T103: Detect unused MVs
        unused_mvs = [mv for mv in all_mvs if mv not in mv_usage_by_name]

        # Calculate aggregate statistics
        aggregate_stats = self._calculate_aggregates(mv_impacts)

        self._logger.info(
            "Impact report generated",
            extra={
                "report_id": report_id,
                "total_mvs_analyzed": len(mv_impacts),
                "unused_mvs": len(unused_mvs),
                "total_bytes_saved": aggregate_stats["total_bytes_saved"],
                "total_slot_ms_saved": aggregate_stats["total_slot_ms_saved"],
                "total_dollars_saved": aggregate_stats["total_dollars_saved"],
            },
        )

        return ImpactReport(
            report_id=report_id,
            generated_at=generated_at,
            period_start=period_start,
            period_end=period_end,
            baseline_mode=baseline_mode,
            baseline_period_start=baseline_period_start,
            baseline_period_end=baseline_period_end,
            mv_impacts=mv_impacts,
            total_bytes_saved=aggregate_stats["total_bytes_saved"],
            total_slot_ms_saved=aggregate_stats["total_slot_ms_saved"],
            total_dollars_saved_on_demand_equiv=aggregate_stats["total_dollars_saved"],
            total_queries_accelerated=aggregate_stats["total_queries_accelerated"],
            matching_executions_count=aggregate_stats["matching_executions_count"],
            unused_mvs=unused_mvs,
        )

    async def _query_mv_usage(
        self,
        start_date: datetime,
        end_date: datetime,
        mv_name_filter: str | None = None,
    ) -> list[dict]:
        """T098: Query MV usage via materialized_view_statistics.

        Args:
            start_date: Start of period
            end_date: End of period
            mv_name_filter: Optional MV name filter

        Returns:
            List of job records with MV usage
        """
        self._logger.info(
            "Querying MV usage statistics",
            extra={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "mv_name_filter": mv_name_filter,
            },
        )

        result = await self._client.query_materialized_view_statistics(
            start_date=start_date,
            end_date=end_date,
            mv_name=mv_name_filter,
            chosen_only=True,
        )

        jobs = result.rows or []
        self._logger.info(f"Found {len(jobs)} jobs with MV usage")

        return jobs

    def _group_usage_by_mv(self, mv_usage: list[dict]) -> dict[str, list[dict]]:
        """Group MV usage jobs by materialized view name.

        Args:
            mv_usage: List of job records with MV usage

        Returns:
            Dictionary mapping MV name to list of jobs
        """
        grouped: dict[str, list[dict]] = defaultdict(list)

        for job in mv_usage:
            mv_name = job.get("mv_name")
            if mv_name:
                grouped[mv_name].append(job)

        return dict(grouped)

    async def _get_all_materialized_views(self, mv_name_filter: str | None = None) -> list[str]:
        """Get list of all materialized views from metadata table.

        Args:
            mv_name_filter: Optional MV name filter

        Returns:
            List of MV names
        """
        try:
            # Query automv_metadata table for all MVs
            sql = """
                SELECT DISTINCT mv_name
                FROM `{project}.{dataset}.automv_metadata`
                WHERE status != 'dropped'
            """.format(project=self._client.project_id, dataset=self._client.dataset_id or "automv_meta")

            if mv_name_filter:
                sql += f" AND mv_name LIKE '%{mv_name_filter}%'"

            result = await self._client.run_query(sql)
            return [row.get("mv_name", "") for row in result.rows] if result.rows else []
        except Exception as e:
            self._logger.warning(f"Failed to query metadata table: {e}")
            return []

    async def _get_mv_metadata(self, mv_name: str) -> dict | None:
        """Get metadata for a specific materialized view.

        Args:
            mv_name: Materialized view name

        Returns:
            Metadata dictionary or None
        """
        try:
            # Extract table_id from mv_name
            if mv_name.count(".") == 2:
                _, _, table_id = mv_name.split(".")
            elif mv_name.count(".") == 1:
                _, table_id = mv_name.split(".")
            else:
                table_id = mv_name

            # Query automv_metadata table for MV details
            sql = """
                SELECT *
                FROM `{project}.{dataset}.automv_metadata`
                WHERE mv_name = @mv_name
                LIMIT 1
            """.format(project=self._client.project_id, dataset=self._client.dataset_id or "automv_meta")

            result = await self._client.run_query(
                sql,
                query_params=[("mv_name", "STRING", table_id)],
            )

            if result.rows and len(result.rows) > 0:
                return result.rows[0]
            return None
        except Exception as e:
            self._logger.warning(f"Failed to query MV metadata for {mv_name}: {e}")
            return None

    async def _query_baseline_metrics(
        self,
        baseline_start: datetime,
        baseline_end: datetime,
        mv_names: list[str],
        mv_name_filter: str | None = None,
    ) -> dict[str, PeriodMetrics]:
        """Query baseline period metrics for MVs.

        Args:
            baseline_start: Start of baseline period
            baseline_end: End of baseline period
            mv_names: List of MV names to query
            mv_name_filter: Optional MV name filter

        Returns:
            Dictionary mapping MV name to baseline metrics
        """
        self._logger.info(
            "Querying baseline metrics",
            extra={
                "baseline_start": baseline_start.isoformat(),
                "baseline_end": baseline_end.isoformat(),
                "mv_count": len(mv_names),
            },
        )

        # T099: Query matching executions for baseline
        baseline_metrics: dict[str, PeriodMetrics] = {}

        for mv_name in mv_names:
            # Query jobs with same normalized_literals hash for baseline period
            # This would use the source_query_hash to find matching executions
            # For now, we'll use a placeholder approach

            # In production, this would:
            # 1. Get source_query_hash from metadata
            # 2. Query INFORMATION_SCHEMA.JOBS for jobs with that hash
            # 3. Calculate aggregate metrics

            baseline_metrics[mv_name] = PeriodMetrics()

        return baseline_metrics

    async def _query_matching_executions(
        self,
        start_date: datetime,
        end_date: datetime,
        source_query_hash: str | None = None,
    ) -> int:
        """T099: Query matching executions for a query family.

        Args:
            start_date: Start of period
            end_date: End of period
            source_query_hash: Query hash to match

        Returns:
            Count of matching executions
        """
        if not source_query_hash:
            return 0

        # Query INFORMATION_SCHEMA.JOBS for jobs with matching normalized_literals
        result = await self._client.query_information_schema_jobs(
            start_date=start_date,
            end_date=end_date,
            additional_filters=[f"query_info.query_hashes.normalized_literals = '{source_query_hash}'"],
            project_filter=self._client.project_id,
            statement_types=["SELECT"],
        )

        return len(result.rows) if result.rows else 0

    async def _query_direct_mv_usage(
        self,
        start_date: datetime,
        end_date: datetime,
        mv_name: str,
    ) -> int:
        """Query direct MV usage (queries that directly SELECT from the MV).

        Args:
            start_date: Start of period
            end_date: End of period
            mv_name: Materialized view name

        Returns:
            Count of direct MV queries
        """
        # Extract table_id from mv_name for the filter
        if mv_name.count(".") == 2:
            _, _, table_id = mv_name.split(".")
        elif mv_name.count(".") == 1:
            _, table_id = mv_name.split(".")
        else:
            table_id = mv_name

        # Build filter for direct MV references in FROM clause
        # This checks if the query references the MV directly
        from_pattern = f"REGEXP_CONTAINS(query, r'FROM[\\\\s]+`?{table_id}`?')"
        join_pattern = f"REGEXP_CONTAINS(query, r'JOIN[\\\\s]+`?{table_id}`?')"
        direct_filter = f"{from_pattern} OR {join_pattern}"

        result = await self._client.query_information_schema_jobs(
            start_date=start_date,
            end_date=end_date,
            additional_filters=[direct_filter],
            project_filter=self._client.project_id,
            statement_types=["SELECT"],
        )

        return len(result.rows) if result.rows else 0

    def _calculate_period_metrics(self, jobs: list[dict]) -> PeriodMetrics:
        """Calculate metrics for a list of jobs.

        Args:
            jobs: List of job records

        Returns:
            PeriodMetrics with aggregate statistics
        """
        total_bytes = sum(job.get("total_bytes_processed", 0) or 0 for job in jobs)
        total_slot_ms = sum(job.get("total_slot_ms", 0) or 0 for job in jobs)
        execution_count = len(jobs)

        return PeriodMetrics(
            total_bytes_processed=total_bytes,
            total_slot_ms=total_slot_ms,
            execution_count=execution_count,
            avg_bytes_processed=total_bytes / execution_count if execution_count > 0 else 0.0,
            avg_slot_ms=total_slot_ms / execution_count if execution_count > 0 else 0.0,
        )

    def _calculate_savings(
        self,
        current_metrics: PeriodMetrics,
        baseline_metrics: PeriodMetrics | None,
        smart_tuning_usage_count: int,
        matching_executions_count: int,
    ) -> dict:
        """T101: Calculate savings from MV usage.

        Args:
            current_metrics: Current period metrics
            baseline_metrics: Baseline period metrics (if available)
            smart_tuning_usage_count: Number of Smart Tuning usages
            matching_executions_count: Total matching executions

        Returns:
            Dictionary with savings statistics
        """
        # Calculate baseline averages
        baseline_avg_bytes = baseline_metrics.avg_bytes_processed if baseline_metrics else 0.0
        baseline_avg_slot_ms = baseline_metrics.avg_slot_ms if baseline_metrics else 0.0

        # Calculate current averages
        current_avg_bytes = current_metrics.avg_bytes_processed
        current_avg_slot_ms = current_metrics.avg_slot_ms

        # Calculate savings per execution (T101)
        # bytes_saved = max(0, baseline - current)
        bytes_saved_per_execution = max(0, baseline_avg_bytes - current_avg_bytes)
        slot_ms_saved_per_execution = max(0, baseline_avg_slot_ms - current_avg_slot_ms)

        # Calculate total savings
        total_bytes_saved = int(bytes_saved_per_execution * smart_tuning_usage_count)
        total_slot_ms_saved = int(slot_ms_saved_per_execution * smart_tuning_usage_count)

        # Calculate dollar savings
        tib_saved = total_bytes_saved / (1024**4)
        dollars_saved = tib_saved * self._impact_config.price_per_tib

        # Calculate usage percentage
        usage_percentage = (
            (smart_tuning_usage_count / matching_executions_count * 100.0) if matching_executions_count > 0 else 0.0
        )

        return {
            "bytes_saved_per_execution": int(bytes_saved_per_execution),
            "slot_ms_saved_per_execution": int(slot_ms_saved_per_execution),
            "total_bytes_saved": total_bytes_saved,
            "total_slot_ms_saved": total_slot_ms_saved,
            "dollars_saved": dollars_saved,
            "usage_percentage": usage_percentage,
        }

    def _determine_attribution_method(
        self,
        total_mvs: int,
        mv_usage_count: int,
    ) -> str:
        """T102: Determine attribution method for savings.

        Args:
            total_mvs: Total number of MVs involved
            mv_usage_count: Number of usages for this MV

        Returns:
            Attribution method: "bytes_proportional", "even_split", or "unknown"
        """
        if total_mvs == 1:
            # Single MV, no attribution needed
            return "bytes_proportional"

        if mv_usage_count > 0:
            # Multiple MVs with usage - use bytes proportional
            return "bytes_proportional"

        # No usage or unclear scenario
        return "unknown"

    def _parse_mv_status(self, status_str: str) -> MVStatus:
        """Parse MV status string to enum.

        Args:
            status_str: Status string

        Returns:
            MVStatus enum value
        """
        status_map = {
            "proposed": MVStatus.PROPOSED,
            "created": MVStatus.CREATED,
            "active": MVStatus.ACTIVE,
            "stale": MVStatus.STALE,
            "invalid": MVStatus.INVALID,
            "deprecated": MVStatus.DEPRECATED,
            "dropped": MVStatus.DROPPED,
        }

        return status_map.get(status_str.lower(), MVStatus.PROPOSED)

    def _calculate_aggregates(self, mv_impacts: list[MVImpact]) -> dict:
        """Calculate aggregate statistics across all MVs.

        Args:
            mv_impacts: List of MV impact records

        Returns:
            Dictionary with aggregate statistics
        """
        total_bytes_saved = sum(mv.total_bytes_saved for mv in mv_impacts)
        total_slot_ms_saved = sum(mv.total_slot_ms_saved for mv in mv_impacts)
        total_dollars_saved = sum(mv.dollars_saved_on_demand_equiv for mv in mv_impacts)
        total_queries_accelerated = sum(mv.smart_tuning_usage_count for mv in mv_impacts)
        matching_executions_count = sum(mv.matching_executions_count for mv in mv_impacts)

        return {
            "total_bytes_saved": total_bytes_saved,
            "total_slot_ms_saved": total_slot_ms_saved,
            "total_dollars_saved": total_dollars_saved,
            "total_queries_accelerated": total_queries_accelerated,
            "matching_executions_count": matching_executions_count,
        }
