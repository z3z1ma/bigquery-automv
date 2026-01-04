"""Reporter service for cost optimization analysis and reporting.

This module provides the ReporterService for generating cost analysis reports
for query candidates, including historical spend, projections, and Smart Tuning
savings estimates.
"""

from dataclasses import dataclass
from datetime import datetime

from bigquery_automv.lib.config import ImpactScoringConfig
from bigquery_automv.lib.logging import get_logger
from bigquery_automv.models.cost_analysis import CostAnalysisResult
from bigquery_automv.models.query_candidate import QueryCandidate
from bigquery_automv.services.analyzer import AnalyzerService


@dataclass
class ReportSummary:
    """Summary statistics for a cost optimization report.

    Attributes:
        total_queries: Number of queries analyzed
        total_historical_spend: Total historical spend across all queries
        total_estimated_monthly_savings: Total estimated monthly savings
        total_estimated_yearly_savings: Total estimated yearly savings
        smart_tuning_eligible_count: Number of queries eligible for Smart Tuning
        avg_confidence: Average confidence level
    """

    total_queries: int
    total_historical_spend: float
    total_estimated_monthly_savings: float
    total_estimated_yearly_savings: float
    smart_tuning_eligible_count: int
    avg_confidence: str


class ReporterService:
    """Service for generating cost optimization reports.

    This service analyzes query candidates to generate cost analysis reports
    with historical spend, projections, and Smart Tuning savings estimates.

    Features:
        - Historical spend calculation (T057)
        - Projected spend calculation (T058)
        - Estimated savings calculation (T059)
        - Confidence level calculation (T060)
        - Custom pricing support (T063)
        - Multiple output formats (T062)
    """

    # Default Smart Tuning savings percentage (70-90% range, default 80%)
    DEFAULT_SMART_TUNING_SAVINGS_PERCENTAGE = 0.8

    def __init__(
        self,
        analyzer: AnalyzerService,
        *,
        pricing_config: ImpactScoringConfig | None = None,
    ) -> None:
        """Initialize the reporter service.

        Args:
            analyzer: AnalyzerService instance for fetching candidate data
            pricing_config: Pricing configuration for cost calculations
        """
        self._analyzer = analyzer
        self._pricing_config = pricing_config or ImpactScoringConfig()
        self._logger = get_logger("reporter")

    async def analyze_query_costs(
        self,
        query_hash: str,
        start_date: datetime,
        end_date: datetime,
        *,
        project_id: str,
        price_per_tib: float | None = None,
    ) -> CostAnalysisResult:
        """Analyze costs for a single query candidate.

        Args:
            query_hash: Hash of the query to analyze
            start_date: Start of analysis period
            end_date: End of analysis period
            project_id: Project ID to analyze
            price_per_tib: Custom price per TiB (overrides config)

        Returns:
            CostAnalysisResult with cost breakdown and projections

        Raises:
            ValueError: If query_hash not found in candidates
            BigQueryClientError: If query execution fails
        """
        # Fetch candidate data using AnalyzerService (T064)
        candidates = await self._fetch_candidates_by_hash(
            [query_hash],
            start_date,
            end_date,
            project_id=project_id,
        )

        if not candidates:
            msg = f"Query hash {query_hash} not found in analysis period"
            raise ValueError(msg)

        candidate = candidates[0]

        # Use custom pricing if provided (T063)
        actual_price_per_tib = price_per_tib or self._pricing_config.price_per_tib

        # Calculate days analyzed
        days_analyzed = self._calculate_days_analyzed(candidate.first_seen, candidate.last_seen)

        # Calculate historical spend (T057)
        historical_spend = self._calculate_historical_spend(
            candidate.bytes_billed_total,
            actual_price_per_tib,
        )

        # Calculate daily average
        daily_avg_spend = historical_spend / days_analyzed if days_analyzed > 0 else 0.0

        # Calculate projected spend (T058)
        projected_monthly, projected_yearly = self._calculate_projected_spend(daily_avg_spend)

        # Calculate estimated savings (T059)
        (
            savings_percentage,
            monthly_savings,
            yearly_savings,
        ) = self._calculate_estimated_savings(
            candidate.smart_tuning_eligible,
            projected_monthly,
            projected_yearly,
        )

        # Calculate confidence level (T060)
        confidence = self._calculate_confidence_level(candidate.execution_count)

        return CostAnalysisResult(
            query_hash=query_hash,
            historical_spend_usd=historical_spend,
            days_analyzed=days_analyzed,
            daily_avg_spend=daily_avg_spend,
            projected_monthly_spend=projected_monthly,
            projected_yearly_spend=projected_yearly,
            smart_tuning_eligible=candidate.smart_tuning_eligible,
            estimated_savings_percentage=savings_percentage,
            estimated_monthly_savings_usd=monthly_savings,
            estimated_yearly_savings_usd=yearly_savings,
            price_per_tib=actual_price_per_tib,
            confidence=confidence,
        )

    async def analyze_multiple_queries(
        self,
        query_hashes: list[str],
        start_date: datetime,
        end_date: datetime,
        *,
        project_id: str,
        price_per_tib: float | None = None,
    ) -> list[CostAnalysisResult]:
        """Analyze costs for multiple query candidates.

        Args:
            query_hashes: List of query hashes to analyze
            start_date: Start of analysis period
            end_date: End of analysis period
            project_id: Project ID to analyze
            price_per_tib: Custom price per TiB (overrides config)

        Returns:
            List of CostAnalysisResult objects

        Raises:
            BigQueryClientError: If query execution fails
        """
        results = []

        for query_hash in query_hashes:
            try:
                result = await self.analyze_query_costs(
                    query_hash,
                    start_date,
                    end_date,
                    project_id=project_id,
                    price_per_tib=price_per_tib,
                )
                results.append(result)
            except ValueError as e:
                # Log but continue with other hashes (T068)
                self._logger.warning(f"Skipping query hash {query_hash}: {e}")
                continue

        return results

    def generate_summary(self, results: list[CostAnalysisResult]) -> ReportSummary:
        """Generate summary statistics from cost analysis results.

        Args:
            results: List of CostAnalysisResult objects

        Returns:
            ReportSummary with aggregated statistics
        """
        if not results:
            return ReportSummary(
                total_queries=0,
                total_historical_spend=0.0,
                total_estimated_monthly_savings=0.0,
                total_estimated_yearly_savings=0.0,
                smart_tuning_eligible_count=0,
                avg_confidence="N/A",
            )

        total_queries = len(results)
        total_historical = sum(r.historical_spend_usd for r in results)
        total_monthly_savings = sum(r.estimated_monthly_savings_usd for r in results)
        total_yearly_savings = sum(r.estimated_yearly_savings_usd for r in results)
        eligible_count = sum(1 for r in results if r.smart_tuning_eligible)

        # Calculate average confidence
        confidence_scores = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        avg_confidence_score = sum(confidence_scores.get(r.confidence, 0) for r in results) / total_queries
        if avg_confidence_score >= 2.5:
            avg_confidence = "HIGH"
        elif avg_confidence_score >= 1.5:
            avg_confidence = "MEDIUM"
        else:
            avg_confidence = "LOW"

        return ReportSummary(
            total_queries=total_queries,
            total_historical_spend=total_historical,
            total_estimated_monthly_savings=total_monthly_savings,
            total_estimated_yearly_savings=total_yearly_savings,
            smart_tuning_eligible_count=eligible_count,
            avg_confidence=avg_confidence,
        )

    def format_markdown(self, results: list[CostAnalysisResult], summary: ReportSummary) -> str:
        """Format results as Markdown table (T062, T065).

        Args:
            results: List of CostAnalysisResult objects
            summary: ReportSummary statistics

        Returns:
            Markdown formatted string
        """
        lines = []

        # Add summary section (T067)
        lines.append("# Cost Optimization Report\n")
        lines.append("## Summary\n")
        lines.append(f"- **Total Queries Analyzed**: {summary.total_queries}")
        lines.append(f"- **Total Historical Spend**: ${summary.total_historical_spend:,.2f}")
        lines.append(f"- **Estimated Monthly Savings**: ${summary.total_estimated_monthly_savings:,.2f}")
        lines.append(f"- **Estimated Yearly Savings**: ${summary.total_estimated_yearly_savings:,.2f}")
        lines.append(f"- **Smart Tuning Eligible**: {summary.smart_tuning_eligible_count}/{summary.total_queries}")
        lines.append(f"- **Average Confidence**: {summary.avg_confidence}\n")

        # Add detailed table (T065)
        lines.append("## Detailed Analysis\n")
        lines.append(
            "| Query Hash | Historical Spend | Days | Daily Avg | Monthly Projected | Yearly Projected | "
            "Eligible | Est. Savings | Monthly Savings | Yearly Savings | Confidence |"
        )
        lines.append(
            "|------------|------------------|------|-----------|-------------------|------------------|"
            "|----------|---------------|-----------------|----------------|------------|",
        )

        for r in results:
            eligible = "Yes" if r.smart_tuning_eligible else "No"
            savings_pct = f"{r.estimated_savings_percentage * 100:.0f}%"

            lines.append(
                f"| `{r.query_hash[:16]}` | ${r.historical_spend_usd:,.2f} | {r.days_analyzed} | "
                f"${r.daily_avg_spend:,.2f} | ${r.projected_monthly_spend:,.2f} | "
                f"${r.projected_yearly_spend:,.2f} | {eligible} | {savings_pct} | "
                f"${r.estimated_monthly_savings_usd:,.2f} | ${r.estimated_yearly_savings_usd:,.2f} | "
                f"{r.confidence} |"
            )

        return "\n".join(lines)

    def format_json(self, results: list[CostAnalysisResult], summary: ReportSummary) -> str:
        """Format results as JSON (T062).

        Args:
            results: List of CostAnalysisResult objects
            summary: ReportSummary statistics

        Returns:
            JSON formatted string
        """
        import json

        data = {
            "summary": {
                "total_queries": summary.total_queries,
                "total_historical_spend_usd": summary.total_historical_spend,
                "total_estimated_monthly_savings_usd": summary.total_estimated_monthly_savings,
                "total_estimated_yearly_savings_usd": summary.total_estimated_yearly_savings,
                "smart_tuning_eligible_count": summary.smart_tuning_eligible_count,
                "avg_confidence": summary.avg_confidence,
            },
            "queries": [],
        }

        for r in results:
            query_data = {
                "query_hash": r.query_hash,
                "historical_spend_usd": r.historical_spend_usd,
                "days_analyzed": r.days_analyzed,
                "daily_avg_spend_usd": r.daily_avg_spend,
                "projected_monthly_spend_usd": r.projected_monthly_spend,
                "projected_yearly_spend_usd": r.projected_yearly_spend,
                "smart_tuning_eligible": r.smart_tuning_eligible,
                "estimated_savings_percentage": r.estimated_savings_percentage,
                "estimated_monthly_savings_usd": r.estimated_monthly_savings_usd,
                "estimated_yearly_savings_usd": r.estimated_yearly_savings_usd,
                "price_per_tib": r.price_per_tib,
                "confidence": r.confidence,
            }
            data["queries"].append(query_data)

        return json.dumps(data, indent=2)

    def format_csv(self, results: list[CostAnalysisResult], summary: ReportSummary) -> str:
        """Format results as CSV (T062).

        Args:
            results: List of CostAnalysisResult objects
            summary: ReportSummary statistics (not used in CSV output)

        Returns:
            CSV formatted string
        """
        import csv
        from io import StringIO

        output = StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(
            [
                "query_hash",
                "historical_spend_usd",
                "days_analyzed",
                "daily_avg_spend_usd",
                "projected_monthly_spend_usd",
                "projected_yearly_spend_usd",
                "smart_tuning_eligible",
                "estimated_savings_percentage",
                "estimated_monthly_savings_usd",
                "estimated_yearly_savings_usd",
                "price_per_tib",
                "confidence",
            ]
        )

        # Write data rows
        for r in results:
            writer.writerow(
                [
                    r.query_hash,
                    f"{r.historical_spend_usd:.2f}",
                    r.days_analyzed,
                    f"{r.daily_avg_spend:.2f}",
                    f"{r.projected_monthly_spend:.2f}",
                    f"{r.projected_yearly_spend:.2f}",
                    r.smart_tuning_eligible,
                    f"{r.estimated_savings_percentage:.4f}",
                    f"{r.estimated_monthly_savings_usd:.2f}",
                    f"{r.estimated_yearly_savings_usd:.2f}",
                    f"{r.price_per_tib:.2f}",
                    r.confidence,
                ]
            )

        return output.getvalue()

    async def _fetch_candidates_by_hash(
        self,
        query_hashes: list[str],
        start_date: datetime,
        end_date: datetime,
        *,
        project_id: str,
    ) -> list[QueryCandidate]:
        """Fetch query candidates by hash using AnalyzerService.

        Args:
            query_hashes: List of query hashes to fetch
            start_date: Start of analysis period
            end_date: End of analysis period
            project_id: Project ID to analyze

        Returns:
            List of QueryCandidate objects

        Raises:
            BigQueryClientError: If query execution fails
        """
        # Use AnalyzerService to analyze and get candidates
        result = await self._analyzer.analyze(
            start_date,
            end_date,
            project_id=project_id,
        )

        # Filter by requested hashes
        hash_set = set(query_hashes)
        matching_candidates = [c for c in result.candidates if c.query_hash in hash_set]

        return matching_candidates

    def _calculate_days_analyzed(self, first_seen: datetime, last_seen: datetime) -> int:
        """Calculate the number of days in the analysis period.

        Args:
            first_seen: First time query was observed
            last_seen: Most recent time query was observed

        Returns:
            Number of days (minimum 1)
        """
        delta = last_seen - first_seen
        days = max(1, delta.days)
        return days

    def _calculate_historical_spend(self, bytes_billed: int, price_per_tib: float) -> float:
        """Calculate historical spend from bytes billed (T057).

        Formula: (bytes_billed / 2^40) * price_per_tib

        Args:
            bytes_billed: Total bytes billed
            price_per_tib: Price per TiB in USD

        Returns:
            Historical spend in USD
        """
        tib_billed = bytes_billed / (1024**4)
        return tib_billed * price_per_tib

    def _calculate_projected_spend(self, daily_avg_spend: float) -> tuple[float, float]:
        """Calculate projected spend from daily average (T058).

        Args:
            daily_avg_spend: Average daily spend in USD

        Returns:
            Tuple of (projected_monthly, projected_yearly) in USD
        """
        projected_monthly = daily_avg_spend * 30
        projected_yearly = daily_avg_spend * 365
        return projected_monthly, projected_yearly

    def _calculate_estimated_savings(
        self,
        smart_tuning_eligible: bool,
        projected_monthly: float,
        projected_yearly: float,
    ) -> tuple[float, float, float]:
        """Calculate estimated Smart Tuning savings (T059).

        Args:
            smart_tuning_eligible: True if query is eligible for Smart Tuning
            projected_monthly: Projected monthly spend in USD
            projected_yearly: Projected yearly spend in USD

        Returns:
            Tuple of (savings_percentage, monthly_savings, yearly_savings)
        """
        if not smart_tuning_eligible:
            return 0.0, 0.0, 0.0

        savings_percentage = self.DEFAULT_SMART_TUNING_SAVINGS_PERCENTAGE
        monthly_savings = projected_monthly * savings_percentage
        yearly_savings = projected_yearly * savings_percentage

        return savings_percentage, monthly_savings, yearly_savings

    def _calculate_confidence_level(self, execution_count: int) -> str:
        """Calculate confidence level based on execution count (T060).

        Args:
            execution_count: Number of times query was executed

        Returns:
            Confidence level: 'HIGH', 'MEDIUM', or 'LOW'
        """
        if execution_count >= 100:
            return "HIGH"
        if execution_count >= 10:
            return "MEDIUM"
        return "LOW"
