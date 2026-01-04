"""Unit tests for Reporter service."""

from datetime import UTC, datetime

import pytest

from bigquery_automv.lib.config import ImpactScoringConfig
from bigquery_automv.models.cost_analysis import CostAnalysisResult
from bigquery_automv.models.query_candidate import QueryCandidate, TableReference
from bigquery_automv.services.analyzer import AnalyzerService
from bigquery_automv.services.reporter import ReporterService, ReportSummary


@pytest.fixture
def reporter_service(mock_bq_client):
    """Create ReporterService instance."""
    analyzer = AnalyzerService(client=mock_bq_client)
    return ReporterService(analyzer=analyzer, pricing_config=ImpactScoringConfig())


@pytest.fixture
def sample_candidate():
    """Create a sample QueryCandidate for testing."""
    return QueryCandidate(
        query_hash="test_hash_123",
        representative_query="SELECT user_id, COUNT(*) FROM `project.dataset.table` GROUP BY user_id",
        execution_count=100,
        bytes_billed_total=10737418240,  # 10 GiB
        total_bytes_processed=16106127360,  # 15 GiB
        slot_ms_total=5000000,
        impact_score=67.11,
        dollar_cost_est_on_demand=67.11,
        impact_model_version="v1.0",
        rulebook_version="v1.0",
        first_seen=datetime(2024, 1, 1, tzinfo=UTC),
        last_seen=datetime(2024, 1, 31, tzinfo=UTC),
        referenced_tables=[
            TableReference(
                project_id="project",
                dataset_id="dataset",
                table_id="table",
                region="US",
            )
        ],
        statement_type="SELECT",
        smart_tuning_eligible=True,
        eligibility_basis="stable",
    )


@pytest.fixture
def sample_analysis_result(mock_bq_client, sample_candidate):
    """Create a sample analysis result for testing."""
    from bigquery_automv.services.analyzer import AnalysisMetrics, AnalysisResult

    # Mock analyzer to return our sample candidate
    async def mock_analyze(*args, **kwargs):
        return AnalysisResult(
            candidates=[sample_candidate],
            metrics=AnalysisMetrics(total_jobs_scanned=1000, families_analyzed=1),
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="test-project",
            region="US",
        )

    mock_bq_client.query_information_schema_jobs = mock_analyze
    return sample_candidate


class TestHistoricalSpendCalculation:
    """Test historical spend calculation (T057)."""

    def test_calculate_historical_spend(self, reporter_service):
        """Test calculation of historical spend from bytes billed."""
        bytes_billed = 10737418240  # 10 GiB
        price_per_tib = 6.25

        historical_spend = reporter_service._calculate_historical_spend(bytes_billed, price_per_tib)

        expected_tib = bytes_billed / (1024**4)
        expected_spend = expected_tib * price_per_tib
        assert historical_spend == pytest.approx(expected_spend, rel=1e-9)
        assert historical_spend > 0

    def test_calculate_historical_spend_zero_bytes(self, reporter_service):
        """Test historical spend with zero bytes."""
        historical_spend = reporter_service._calculate_historical_spend(0, 6.25)
        assert historical_spend == 0.0


class TestProjectedSpendCalculation:
    """Test projected spend calculation (T058)."""

    def test_calculate_projected_spend(self, reporter_service):
        """Test calculation of projected monthly and yearly spend."""
        daily_avg_spend = 10.0  # $10 per day

        projected_monthly, projected_yearly = reporter_service._calculate_projected_spend(daily_avg_spend)

        assert projected_monthly == pytest.approx(300.0)  # 30 days
        assert projected_yearly == pytest.approx(3650.0)  # 365 days

    def test_calculate_projected_spend_zero_daily(self, reporter_service):
        """Test projected spend with zero daily average."""
        monthly, yearly = reporter_service._calculate_projected_spend(0.0)
        assert monthly == 0.0
        assert yearly == 0.0


class TestEstimatedSavingsCalculation:
    """Test estimated savings calculation (T059)."""

    def test_calculate_estimated_savings_eligible(self, reporter_service):
        """Test savings calculation for Smart Tuning eligible query."""
        savings_pct, monthly_savings, yearly_savings = reporter_service._calculate_estimated_savings(
            smart_tuning_eligible=True,
            projected_monthly=100.0,
            projected_yearly=1200.0,
        )

        assert savings_pct == 0.8  # 80% default
        assert monthly_savings == pytest.approx(80.0)
        assert yearly_savings == pytest.approx(960.0)

    def test_calculate_estimated_savings_ineligible(self, reporter_service):
        """Test savings calculation for ineligible query."""
        savings_pct, monthly_savings, yearly_savings = reporter_service._calculate_estimated_savings(
            smart_tuning_eligible=False,
            projected_monthly=100.0,
            projected_yearly=1200.0,
        )

        assert savings_pct == 0.0
        assert monthly_savings == 0.0
        assert yearly_savings == 0.0


class TestConfidenceLevelCalculation:
    """Test confidence level calculation (T060)."""

    def test_calculate_confidence_high(self, reporter_service):
        """Test HIGH confidence for >= 100 executions."""
        confidence = reporter_service._calculate_confidence_level(150)
        assert confidence == "HIGH"

    def test_calculate_confidence_medium(self, reporter_service):
        """Test MEDIUM confidence for 10-99 executions."""
        confidence = reporter_service._calculate_confidence_level(50)
        assert confidence == "MEDIUM"

    def test_calculate_confidence_low(self, reporter_service):
        """Test LOW confidence for < 10 executions."""
        confidence = reporter_service._calculate_confidence_level(5)
        assert confidence == "LOW"

    def test_calculate_confidence_boundary_high(self, reporter_service):
        """Test confidence at exact boundary (100)."""
        confidence = reporter_service._calculate_confidence_level(100)
        assert confidence == "HIGH"

    def test_calculate_confidence_boundary_medium(self, reporter_service):
        """Test confidence at exact boundary (10)."""
        confidence = reporter_service._calculate_confidence_level(10)
        assert confidence == "MEDIUM"


class TestDaysAnalyzedCalculation:
    """Test days analyzed calculation."""

    def test_calculate_days_analyzed_single_day(self, reporter_service):
        """Test days analyzed for same day."""
        first_seen = datetime(2024, 1, 1, tzinfo=UTC)
        last_seen = datetime(2024, 1, 1, tzinfo=UTC)
        days = reporter_service._calculate_days_analyzed(first_seen, last_seen)
        assert days == 1

    def test_calculate_days_analyzed_multiple_days(self, reporter_service):
        """Test days analyzed across multiple days."""
        first_seen = datetime(2024, 1, 1, tzinfo=UTC)
        last_seen = datetime(2024, 1, 31, tzinfo=UTC)
        days = reporter_service._calculate_days_analyzed(first_seen, last_seen)
        assert days == 30


class TestQueryCostAnalysis:
    """Test single query cost analysis."""

    @pytest.mark.asyncio
    async def test_analyze_query_costs_eligible(self, reporter_service, sample_candidate):
        """Test cost analysis for Smart Tuning eligible query."""
        from bigquery_automv.services.analyzer import AnalysisMetrics, AnalysisResult

        # Mock analyzer to return our sample candidate
        async def mock_analyze(*args, **kwargs):
            return AnalysisResult(
                candidates=[sample_candidate],
                metrics=AnalysisMetrics(total_jobs_scanned=1000, families_analyzed=1),
                start_date=datetime(2024, 1, 1, tzinfo=UTC),
                end_date=datetime(2024, 1, 31, tzinfo=UTC),
                project_id="test-project",
                region="US",
            )

        reporter_service._analyzer.analyze = mock_analyze

        result = await reporter_service.analyze_query_costs(
            query_hash=sample_candidate.query_hash,
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="test-project",
        )

        assert result.query_hash == sample_candidate.query_hash
        assert result.historical_spend_usd > 0
        assert result.days_analyzed > 0
        assert result.daily_avg_spend > 0
        assert result.projected_monthly_spend > 0
        assert result.projected_yearly_spend > 0
        assert result.smart_tuning_eligible is True
        assert result.estimated_savings_percentage > 0
        assert result.estimated_monthly_savings_usd > 0
        assert result.estimated_yearly_savings_usd > 0

    @pytest.mark.asyncio
    async def test_analyze_query_costs_ineligible(self, reporter_service, sample_candidate):
        """Test cost analysis for ineligible query."""
        from bigquery_automv.services.analyzer import AnalysisMetrics, AnalysisResult

        # Make query ineligible
        sample_candidate.smart_tuning_eligible = False

        async def mock_analyze(*args, **kwargs):
            return AnalysisResult(
                candidates=[sample_candidate],
                metrics=AnalysisMetrics(total_jobs_scanned=1000, families_analyzed=1),
                start_date=datetime(2024, 1, 1, tzinfo=UTC),
                end_date=datetime(2024, 1, 31, tzinfo=UTC),
                project_id="test-project",
                region="US",
            )

        reporter_service._analyzer.analyze = mock_analyze

        result = await reporter_service.analyze_query_costs(
            query_hash=sample_candidate.query_hash,
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="test-project",
        )

        assert result.smart_tuning_eligible is False
        assert result.estimated_savings_percentage == 0.0
        assert result.estimated_monthly_savings_usd == 0.0
        assert result.estimated_yearly_savings_usd == 0.0

    @pytest.mark.asyncio
    async def test_analyze_query_costs_not_found(self, reporter_service):
        """Test error when query hash not found."""
        from bigquery_automv.services.analyzer import AnalysisMetrics, AnalysisResult

        async def mock_analyze(*args, **kwargs):
            return AnalysisResult(
                candidates=[],  # Empty - no candidates found
                metrics=AnalysisMetrics(total_jobs_scanned=1000, families_analyzed=0),
                start_date=datetime(2024, 1, 1, tzinfo=UTC),
                end_date=datetime(2024, 1, 31, tzinfo=UTC),
                project_id="test-project",
                region="US",
            )

        reporter_service._analyzer.analyze = mock_analyze

        with pytest.raises(ValueError, match="not found in analysis period"):
            await reporter_service.analyze_query_costs(
                query_hash="nonexistent_hash",
                start_date=datetime(2024, 1, 1, tzinfo=UTC),
                end_date=datetime(2024, 1, 31, tzinfo=UTC),
                project_id="test-project",
            )


class TestMultipleQueriesAnalysis:
    """Test multiple query cost analysis."""

    @pytest.mark.asyncio
    async def test_analyze_multiple_queries(self, reporter_service, sample_candidate):
        """Test cost analysis for multiple queries."""
        from bigquery_automv.services.analyzer import AnalysisMetrics, AnalysisResult

        # Create second candidate
        candidate2 = QueryCandidate(
            query_hash="test_hash_456",
            representative_query="SELECT region, COUNT(*) FROM `project.dataset.table` GROUP BY region",
            execution_count=50,
            bytes_billed_total=5368709120,  # 5 GiB
            total_bytes_processed=8053063680,  # 7.5 GiB
            slot_ms_total=2500000,
            impact_score=33.55,
            dollar_cost_est_on_demand=33.55,
            impact_model_version="v1.0",
            rulebook_version="v1.0",
            first_seen=datetime(2024, 1, 1, tzinfo=UTC),
            last_seen=datetime(2024, 1, 31, tzinfo=UTC),
            referenced_tables=sample_candidate.referenced_tables,
            statement_type="SELECT",
            smart_tuning_eligible=True,
            eligibility_basis="stable",
        )

        async def mock_analyze(*args, **kwargs):
            return AnalysisResult(
                candidates=[sample_candidate, candidate2],
                metrics=AnalysisMetrics(total_jobs_scanned=2000, families_analyzed=2),
                start_date=datetime(2024, 1, 1, tzinfo=UTC),
                end_date=datetime(2024, 1, 31, tzinfo=UTC),
                project_id="test-project",
                region="US",
            )

        reporter_service._analyzer.analyze = mock_analyze

        results = await reporter_service.analyze_multiple_queries(
            query_hashes=[sample_candidate.query_hash, candidate2.query_hash],
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="test-project",
        )

        assert len(results) == 2
        assert all(isinstance(r, CostAnalysisResult) for r in results)


class TestSummaryGeneration:
    """Test report summary generation."""

    def test_generate_summary_empty(self, reporter_service):
        """Test summary generation with no results."""
        summary = reporter_service.generate_summary([])
        assert summary.total_queries == 0
        assert summary.total_historical_spend == 0.0
        assert summary.total_estimated_monthly_savings == 0.0
        assert summary.total_estimated_yearly_savings == 0.0
        assert summary.smart_tuning_eligible_count == 0
        assert summary.avg_confidence == "N/A"

    def test_generate_summary_single_query(self, reporter_service):
        """Test summary generation with single query."""
        result = CostAnalysisResult(
            query_hash="test_hash",
            historical_spend_usd=100.0,
            days_analyzed=30,
            daily_avg_spend=3.33,
            projected_monthly_spend=100.0,
            projected_yearly_spend=1200.0,
            smart_tuning_eligible=True,
            estimated_savings_percentage=0.8,
            estimated_monthly_savings_usd=80.0,
            estimated_yearly_savings_usd=960.0,
            confidence="HIGH",
        )

        summary = reporter_service.generate_summary([result])
        assert summary.total_queries == 1
        assert summary.total_historical_spend == 100.0
        assert summary.total_estimated_monthly_savings == 80.0
        assert summary.total_estimated_yearly_savings == 960.0
        assert summary.smart_tuning_eligible_count == 1
        assert summary.avg_confidence == "HIGH"

    def test_generate_summary_multiple_queries(self, reporter_service):
        """Test summary generation with multiple queries."""
        results = [
            CostAnalysisResult(
                query_hash="hash1",
                historical_spend_usd=100.0,
                days_analyzed=30,
                daily_avg_spend=3.33,
                projected_monthly_spend=100.0,
                projected_yearly_spend=1200.0,
                smart_tuning_eligible=True,
                estimated_savings_percentage=0.8,
                estimated_monthly_savings_usd=80.0,
                estimated_yearly_savings_usd=960.0,
                confidence="HIGH",
            ),
            CostAnalysisResult(
                query_hash="hash2",
                historical_spend_usd=50.0,
                days_analyzed=30,
                daily_avg_spend=1.67,
                projected_monthly_spend=50.0,
                projected_yearly_spend=600.0,
                smart_tuning_eligible=False,
                estimated_savings_percentage=0.0,
                estimated_monthly_savings_usd=0.0,
                estimated_yearly_savings_usd=0.0,
                confidence="MEDIUM",
            ),
        ]

        summary = reporter_service.generate_summary(results)
        assert summary.total_queries == 2
        assert summary.total_historical_spend == 150.0
        assert summary.total_estimated_monthly_savings == 80.0
        assert summary.smart_tuning_eligible_count == 1
        # Average confidence: (HIGH=3 + MEDIUM=2) / 2 = 2.5 -> HIGH
        assert summary.avg_confidence == "HIGH"


class TestOutputFormatting:
    """Test output formatting (T062)."""

    def test_format_markdown(self, reporter_service):
        """Test Markdown formatting."""
        result = CostAnalysisResult(
            query_hash="test_hash",
            historical_spend_usd=100.0,
            days_analyzed=30,
            daily_avg_spend=3.33,
            projected_monthly_spend=100.0,
            projected_yearly_spend=1200.0,
            smart_tuning_eligible=True,
            estimated_savings_percentage=0.8,
            estimated_monthly_savings_usd=80.0,
            estimated_yearly_savings_usd=960.0,
            confidence="HIGH",
        )

        summary = ReportSummary(
            total_queries=1,
            total_historical_spend=100.0,
            total_estimated_monthly_savings=80.0,
            total_estimated_yearly_savings=960.0,
            smart_tuning_eligible_count=1,
            avg_confidence="HIGH",
        )

        markdown = reporter_service.format_markdown([result], summary)

        assert "# Cost Optimization Report" in markdown
        assert "## Summary" in markdown
        assert "## Detailed Analysis" in markdown
        assert "Total Queries Analyzed**" in markdown
        assert "test_hash" in markdown
        assert "$100.00" in markdown
        assert "HIGH" in markdown

    def test_format_json(self, reporter_service):
        """Test JSON formatting."""
        result = CostAnalysisResult(
            query_hash="test_hash",
            historical_spend_usd=100.0,
            days_analyzed=30,
            daily_avg_spend=3.33,
            projected_monthly_spend=100.0,
            projected_yearly_spend=1200.0,
            smart_tuning_eligible=True,
            estimated_savings_percentage=0.8,
            estimated_monthly_savings_usd=80.0,
            estimated_yearly_savings_usd=960.0,
            confidence="HIGH",
        )

        summary = ReportSummary(
            total_queries=1,
            total_historical_spend=100.0,
            total_estimated_monthly_savings=80.0,
            total_estimated_yearly_savings=960.0,
            smart_tuning_eligible_count=1,
            avg_confidence="HIGH",
        )

        json_output = reporter_service.format_json([result], summary)

        import json

        data = json.loads(json_output)
        assert "summary" in data
        assert "queries" in data
        assert data["summary"]["total_queries"] == 1
        assert len(data["queries"]) == 1
        assert data["queries"][0]["query_hash"] == "test_hash"

    def test_format_csv(self, reporter_service):
        """Test CSV formatting."""
        result = CostAnalysisResult(
            query_hash="test_hash",
            historical_spend_usd=100.0,
            days_analyzed=30,
            daily_avg_spend=3.33,
            projected_monthly_spend=100.0,
            projected_yearly_spend=1200.0,
            smart_tuning_eligible=True,
            estimated_savings_percentage=0.8,
            estimated_monthly_savings_usd=80.0,
            estimated_yearly_savings_usd=960.0,
            confidence="HIGH",
        )

        summary = ReportSummary(
            total_queries=1,
            total_historical_spend=100.0,
            total_estimated_monthly_savings=80.0,
            total_estimated_yearly_savings=960.0,
            smart_tuning_eligible_count=1,
            avg_confidence="HIGH",
        )

        csv_output = reporter_service.format_csv([result], summary)

        lines = csv_output.strip().split("\n")
        assert len(lines) >= 2  # Header + at least one data row
        assert "query_hash" in lines[0]
        assert "test_hash" in lines[1]  # Data row should contain our hash


class TestCustomPricing:
    """Test custom pricing support (T063)."""

    @pytest.mark.asyncio
    async def test_custom_price_per_tib(self, reporter_service, sample_candidate):
        """Test custom price per TiB."""
        from bigquery_automv.services.analyzer import AnalysisMetrics, AnalysisResult

        async def mock_analyze(*args, **kwargs):
            return AnalysisResult(
                candidates=[sample_candidate],
                metrics=AnalysisMetrics(total_jobs_scanned=1000, families_analyzed=1),
                start_date=datetime(2024, 1, 1, tzinfo=UTC),
                end_date=datetime(2024, 1, 31, tzinfo=UTC),
                project_id="test-project",
                region="US",
            )

        reporter_service._analyzer.analyze = mock_analyze

        # Use custom pricing
        result = await reporter_service.analyze_query_costs(
            query_hash=sample_candidate.query_hash,
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="test-project",
            price_per_tib=10.0,  # Custom price
        )

        assert result.price_per_tib == 10.0
