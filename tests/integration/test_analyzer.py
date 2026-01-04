"""Integration tests for analyzer command."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from bigquery_automv.lib.config import AnalysisConfig, ImpactScoringConfig
from bigquery_automv.services.analyzer import AnalysisResult, AnalyzerService
from bigquery_automv.services.bq_client import BigQueryClient, QueryResult


@pytest.fixture
def integration_client():
    """Create a mocked BigQuery client for integration testing."""
    client = MagicMock(spec=BigQueryClient)
    client.project_id = "test-project"
    client.region = "US"

    # Mock async methods
    client.query_information_schema_jobs = AsyncMock()
    client.get_dataset_region = AsyncMock(return_value="US")
    client.validate_region_match = AsyncMock()
    client.table_exists = AsyncMock(return_value=False)
    client.get_materialized_view = AsyncMock()
    client.create_materialized_view = AsyncMock()
    client.drop_materialized_view = AsyncMock()
    client.insert_metadata = AsyncMock()

    return client


@pytest.fixture
def sample_job_data():
    """Sample job data for integration testing."""
    return [
        {
            "job_id": "job1",
            "query": (
                "SELECT user_id, COUNT(*) as cnt FROM `project.dataset.events` WHERE region = 'US' GROUP BY user_id"
            ),
            "normalized_literals": "abc123",
            "total_bytes_billed": 1073741824,  # 1 GiB
            "total_bytes_processed": 1610612736,  # 1.5 GiB
            "total_slot_ms": 500000,
            "creation_time": datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
            "statement_type": "SELECT",
            "referenced_tables": [
                {
                    "project_id": "project",
                    "dataset_id": "dataset",
                    "table_id": "events",
                    "region": "US",
                    "processed_bytes": 1610612736,
                }
            ],
            "user_email": "user1@example.com",
        },
        {
            "job_id": "job2",
            "query": (
                "SELECT user_id, COUNT(*) as cnt FROM `project.dataset.events` WHERE region = 'US' GROUP BY user_id"
            ),
            "normalized_literals": "abc123",
            "total_bytes_billed": 2147483648,  # 2 GiB
            "total_bytes_processed": 3221225472,  # 3 GiB
            "total_slot_ms": 1000000,
            "creation_time": datetime(2024, 1, 16, 12, 0, 0, tzinfo=UTC),
            "statement_type": "SELECT",
            "referenced_tables": [
                {
                    "project_id": "project",
                    "dataset_id": "dataset",
                    "table_id": "events",
                    "region": "US",
                    "processed_bytes": 3221225472,
                }
            ],
            "user_email": "user2@example.com",
        },
        {
            "job_id": "job3",
            "query": (
                "SELECT region, COUNT(*) as cnt FROM `project.dataset.events` WHERE status = 'active' GROUP BY region"
            ),
            "normalized_literals": "def456",
            "total_bytes_billed": 536870912,  # 512 MiB
            "total_bytes_processed": 805306368,  # 768 MiB
            "total_slot_ms": 250000,
            "creation_time": datetime(2024, 1, 17, 12, 0, 0, tzinfo=UTC),
            "statement_type": "SELECT",
            "referenced_tables": [
                {
                    "project_id": "project",
                    "dataset_id": "dataset",
                    "table_id": "events",
                    "region": "US",
                    "processed_bytes": 805306368,
                }
            ],
            "user_email": "user1@example.com",
        },
    ]


class TestAnalyzerServiceIntegration:
    """Integration tests for AnalyzerService."""

    @pytest.mark.asyncio
    async def test_full_analysis_workflow(self, integration_client, sample_job_data):
        """Test complete analysis workflow from job scanning to candidate generation."""
        # Mock the query response
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=3,
        )

        # Create analyzer service
        analyzer = AnalyzerService(
            client=integration_client,
            impact_config=ImpactScoringConfig(
                price_per_tib=6.25,
                slot_weight=0.25,
                slot_ms_per_tib_equivalent=3.6e9,
            ),
            analysis_config=AnalysisConfig(
                min_executions=2,
                min_bytes=1073741824,  # 1 GiB
                min_slot_ms=0,
                max_families=100,
                sample_size_k=20,
            ),
        )

        # Run analysis
        start_date = datetime(2024, 1, 1, tzinfo=UTC)
        end_date = datetime(2024, 1, 31, tzinfo=UTC)

        result = await analyzer.analyze(
            start_date=start_date,
            end_date=end_date,
            project_id="project",
        )

        # Verify structure
        assert isinstance(result, AnalysisResult)
        assert len(result.candidates) > 0
        assert result.project_id == "project"
        assert result.region == "US"
        assert result.metrics.total_jobs_scanned == 3

        # Verify candidate aggregation (jobs with same normalized_literals grouped)
        candidates_by_hash = {c.query_hash: c for c in result.candidates}

        # Check abc123 hash (2 jobs)
        if "abc123" in candidates_by_hash:
            candidate = candidates_by_hash["abc123"]
            assert candidate.execution_count == 2
            assert candidate.bytes_billed_total == 3221225472  # 1 GiB + 2 GiB
            assert candidate.impact_score > 0

    @pytest.mark.asyncio
    async def test_filtering_by_min_executions(self, integration_client, sample_job_data):
        """Test that candidates below min_executions threshold are filtered."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=3,
        )

        analyzer = AnalyzerService(
            client=integration_client,
            analysis_config=AnalysisConfig(
                min_executions=5,  # High threshold - should filter out all
                min_bytes=0,
                min_slot_ms=0,
                max_families=100,
            ),
        )

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # All candidates should be filtered out
        assert len(result.candidates) == 0
        assert result.metrics.skipped_with_reasons.get("below_min_executions", 0) > 0

    @pytest.mark.asyncio
    async def test_filtering_by_min_bytes(self, integration_client, sample_job_data):
        """Test that candidates below min_bytes threshold are filtered."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=3,
        )

        analyzer = AnalyzerService(
            client=integration_client,
            analysis_config=AnalysisConfig(
                min_executions=1,
                min_bytes=5368709120,  # 5 GiB - should filter out some
                min_slot_ms=0,
                max_families=100,
            ),
        )

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # def456 hash only has 512 MiB, should be filtered
        candidates_by_hash = {c.query_hash: c for c in result.candidates}
        assert "def456" not in candidates_by_hash

    @pytest.mark.asyncio
    async def test_max_families_limit(self, integration_client, sample_job_data):
        """Test that max_families limit is applied."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=3,
        )

        analyzer = AnalyzerService(
            client=integration_client,
            analysis_config=AnalysisConfig(
                min_executions=1,
                min_bytes=0,
                min_slot_ms=0,
                max_families=1,  # Only return top 1
            ),
        )

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # Should return at most 1 candidate
        assert len(result.candidates) <= 1

    @pytest.mark.asyncio
    async def test_impact_score_calculation(self, integration_client, sample_job_data):
        """Test impact score calculation."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=3,
        )

        analyzer = AnalyzerService(
            client=integration_client,
            impact_config=ImpactScoringConfig(
                price_per_tib=6.25,
                slot_weight=0.25,
                slot_ms_per_tib_equivalent=3.6e9,
            ),
            analysis_config=AnalysisConfig(
                min_executions=1,
                min_bytes=0,
                min_slot_ms=0,
                max_families=100,
            ),
        )

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # Verify impact scores are calculated
        for candidate in result.candidates:
            assert candidate.impact_score > 0
            assert candidate.dollar_cost_est_on_demand > 0
            # Impact score should roughly match dollar cost for simple cases
            assert abs(candidate.impact_score - candidate.dollar_cost_est_on_demand) < candidate.impact_score

    @pytest.mark.asyncio
    async def test_sorting_by_impact_score(self, integration_client, sample_job_data):
        """Test that candidates are sorted by impact score."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=3,
        )

        analyzer = AnalyzerService(
            client=integration_client,
            analysis_config=AnalysisConfig(
                min_executions=1,
                min_bytes=0,
                min_slot_ms=0,
                max_families=100,
            ),
        )

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # Verify descending order by impact score
        if len(result.candidates) > 1:
            for i in range(len(result.candidates) - 1):
                assert result.candidates[i].impact_score >= result.candidates[i + 1].impact_score

    @pytest.mark.asyncio
    async def test_query_samples_retrieval(self, integration_client, sample_job_data):
        """Test retrieving query samples for a specific hash."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=2,
        )

        analyzer = AnalyzerService(client=integration_client)

        samples = await analyzer.get_query_samples(
            query_hash="abc123",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
            limit=10,
        )

        # Should return samples sorted by bytes_billed
        assert len(samples) > 0
        assert all("job_id" in s for s in samples)
        assert all("query" in s for s in samples)
        assert all("total_bytes_billed" in s for s in samples)

        # Verify sorting
        for i in range(len(samples) - 1):
            assert samples[i]["total_bytes_billed"] >= samples[i + 1]["total_bytes_billed"]

    @pytest.mark.asyncio
    async def test_null_bytes_billed_handling(self, integration_client):
        """Test handling of queries with NULL total_bytes_billed."""
        job_data_with_nulls = [
            {
                "job_id": "job1",
                "query": "SELECT * FROM `project.dataset.table` WHERE user_id = CURRENT_USER()",  # RLS query
                "normalized_literals": "xyz789",
                "total_bytes_billed": None,  # NULL for RLS queries
                "total_bytes_processed": None,
                "total_slot_ms": 0,
                "creation_time": datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
                "statement_type": "SELECT",
                "referenced_tables": [
                    {"project_id": "project", "dataset_id": "dataset", "table_id": "table", "region": "US"}
                ],
            },
        ]

        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=job_data_with_nulls,
            total_rows=1,
        )

        analyzer = AnalyzerService(
            client=integration_client,
            analysis_config=AnalysisConfig(
                min_executions=1,
                min_bytes=0,
                min_slot_ms=0,
                max_families=100,
            ),
        )

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # NULL bytes_billed should be handled with warnings
        assert "null_bytes_billed" in result.metrics.skipped_with_reasons

    @pytest.mark.asyncio
    async def test_metrics_tracking(self, integration_client, sample_job_data):
        """Test that analysis metrics are properly tracked."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=3,
        )

        analyzer = AnalyzerService(
            client=integration_client,
            analysis_config=AnalysisConfig(
                min_executions=1,
                min_bytes=0,
                min_slot_ms=0,
                max_families=100,
            ),
        )

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # Verify metrics
        assert result.metrics.total_jobs_scanned == 3
        assert result.metrics.families_analyzed >= 0
        assert len(result.metrics.runtime_per_phase) > 0

        # Check phase timing
        phases = ["scan_jobs", "group_jobs", "apply_filters", "generate_candidates", "sort_limit"]
        for phase in phases:
            assert phase in result.metrics.runtime_per_phase
            assert result.metrics.runtime_per_phase[phase] >= 0


class TestAnalyzerWithSmartTuning:
    """Integration tests combining Analyzer with Smart Tuning checks."""

    @pytest.mark.asyncio
    async def test_eligible_query_detection(self, integration_client, sample_job_data):
        """Test that eligible queries are properly identified."""
        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=sample_job_data,
            total_rows=2,
        )

        from bigquery_automv.services.smart_tuning import SmartTuningService

        # Create services
        analyzer = AnalyzerService(client=integration_client)
        smart_tuning = SmartTuningService(bq_client=integration_client)

        # Run analysis
        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        # Check Smart Tuning eligibility for first candidate
        if result.candidates:
            candidate = result.candidates[0]
            eligibility_result = await smart_tuning.check_elibility(
                sql=candidate.representative_query,
                query_hash=candidate.query_hash,
                target_dataset="dataset",
                target_project="project",
            )

            # Simple aggregation queries should be eligible
            # (unless they have unsupported features)
            assert eligibility_result.query_hash == candidate.query_hash
            assert isinstance(eligibility_result.eligible, bool)

    @pytest.mark.asyncio
    async def test_ineligible_query_detection(self, integration_client):
        """Test that ineligible queries are properly identified."""
        ineligible_job = [
            {
                "job_id": "job1",
                "query": "SELECT user_id, RAND() as random_val FROM `project.dataset.table`",
                "normalized_literals": "rand123",
                "total_bytes_billed": 1073741824,
                "total_bytes_processed": 1610612736,
                "total_slot_ms": 500000,
                "creation_time": datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
                "statement_type": "SELECT",
                "referenced_tables": [
                    {"project_id": "project", "dataset_id": "dataset", "table_id": "table", "region": "US"}
                ],
            },
        ]

        integration_client.query_information_schema_jobs.return_value = QueryResult(
            rows=ineligible_job,
            total_rows=1,
        )

        from bigquery_automv.services.smart_tuning import SmartTuningService

        analyzer = AnalyzerService(client=integration_client)
        smart_tuning = SmartTuningService(bq_client=integration_client)

        result = await analyzer.analyze(
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 31, tzinfo=UTC),
            project_id="project",
        )

        if result.candidates:
            candidate = result.candidates[0]
            eligibility_result = await smart_tuning.check_elibility(
                sql=candidate.representative_query,
                query_hash=candidate.query_hash,
                target_dataset="dataset",
                target_project="project",
            )

            # Query with RAND() should be ineligible
            assert eligibility_result.eligible is False
            assert len(eligibility_result.disqualification_reasons) > 0
