"""Unit tests for ImpactService."""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from bigquery_automv.lib.config import ImpactScoringConfig
from bigquery_automv.models.impact_report import ImpactReport
from bigquery_automv.services.bq_client import BigQueryClient, QueryResult
from bigquery_automv.services.impact import ImpactService, PeriodMetrics


@pytest.fixture
def mock_bq_client():
    """Create a mock BigQuery client."""
    client = MagicMock(spec=BigQueryClient)
    client.project_id = "test-project"
    client.region = "region-us"
    return client


@pytest.fixture
def impact_service(mock_bq_client):
    """Create an ImpactService instance."""
    return ImpactService(client=mock_bq_client, impact_config=ImpactScoringConfig())


def test_period_metrics_calculation():
    """Test PeriodMetrics calculation."""
    metrics = PeriodMetrics(
        total_bytes_processed=1000000000,
        total_slot_ms=5000000,
        execution_count=10,
    )

    assert metrics.total_bytes_processed == 1000000000
    assert metrics.total_slot_ms == 5000000
    assert metrics.execution_count == 10
    assert metrics.avg_bytes_processed == 100000000.0
    assert metrics.avg_slot_ms == 500000.0


def test_period_metrics_empty():
    """Test PeriodMetrics with no executions."""
    metrics = PeriodMetrics()

    assert metrics.total_bytes_processed == 0
    assert metrics.total_slot_ms == 0
    assert metrics.execution_count == 0
    assert metrics.avg_bytes_processed == 0.0
    assert metrics.avg_slot_ms == 0.0


@pytest.mark.asyncio
async def test_generate_impact_report_no_usage(impact_service, mock_bq_client):
    """Test impact report generation with no MV usage."""
    # Mock query_materialized_view_statistics to return empty result
    mock_bq_client.query_materialized_view_statistics = AsyncMock(return_value=QueryResult(rows=[], total_rows=0))

    # Mock _get_all_materialized_views
    impact_service._get_all_materialized_views = AsyncMock(return_value=[])

    # Generate report
    report = await impact_service.generate_impact_report(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        baseline_mode="none",
    )

    # Verify report structure
    assert isinstance(report, ImpactReport)
    assert report.report_id is not None
    assert report.mv_impacts == []
    assert report.unused_mvs == []
    assert report.total_bytes_saved == 0
    assert report.total_slot_ms_saved == 0
    assert report.total_dollars_saved_on_demand_equiv == 0.0


@pytest.mark.asyncio
async def test_generate_impact_report_with_usage(impact_service, mock_bq_client):
    """Test impact report generation with MV usage."""
    # Mock MV usage data
    mv_usage_jobs = [
        {
            "job_id": "job1",
            "mv_name": "automv_test123",
            "total_bytes_processed": 1000000000,
            "total_slot_ms": 5000000,
            "creation_time": datetime(2024, 1, 15, 12, 0, 0),
        },
        {
            "job_id": "job2",
            "mv_name": "automv_test123",
            "total_bytes_processed": 800000000,
            "total_slot_ms": 4000000,
            "creation_time": datetime(2024, 1, 16, 12, 0, 0),
        },
    ]

    # Mock query_materialized_view_statistics
    mock_bq_client.query_materialized_view_statistics = AsyncMock(
        return_value=QueryResult(rows=mv_usage_jobs, total_rows=2)
    )

    # Mock _get_all_materialized_views and _get_mv_metadata
    impact_service._get_all_materialized_views = AsyncMock(return_value=["automv_test123"])
    impact_service._get_mv_metadata = AsyncMock(return_value={"source_query_hash": "abc123", "status": "active"})

    # Mock _query_matching_executions
    impact_service._query_matching_executions = AsyncMock(return_value=10)

    # Generate report
    report = await impact_service.generate_impact_report(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        baseline_mode="none",
    )

    # Verify report structure
    assert isinstance(report, ImpactReport)
    assert len(report.mv_impacts) == 1
    assert report.mv_impacts[0].mv_name == "automv_test123"
    assert report.mv_impacts[0].smart_tuning_usage_count == 2
    assert report.mv_impacts[0].current_execution_count == 2


@pytest.mark.asyncio
async def test_group_usage_by_mv(impact_service):
    """Test grouping MV usage by MV name."""
    mv_usage = [
        {"mv_name": "automv_test1", "job_id": "job1"},
        {"mv_name": "automv_test2", "job_id": "job2"},
        {"mv_name": "automv_test1", "job_id": "job3"},
        {"mv_name": "automv_test2", "job_id": "job4"},
        {"mv_name": "automv_test1", "job_id": "job5"},
    ]

    grouped = impact_service._group_usage_by_mv(mv_usage)

    assert len(grouped) == 2
    assert len(grouped["automv_test1"]) == 3
    assert len(grouped["automv_test2"]) == 2


def test_calculate_period_metrics(impact_service):
    """Test period metrics calculation."""
    jobs = [
        {"total_bytes_processed": 1000000000, "total_slot_ms": 5000000},
        {"total_bytes_processed": 2000000000, "total_slot_ms": 10000000},
        {"total_bytes_processed": 1500000000, "total_slot_ms": 7500000},
    ]

    metrics = impact_service._calculate_period_metrics(jobs)

    assert metrics.total_bytes_processed == 4500000000
    assert metrics.total_slot_ms == 22500000
    assert metrics.execution_count == 3
    assert metrics.avg_bytes_processed == 1500000000.0
    assert metrics.avg_slot_ms == 7500000.0


def test_calculate_savings_no_baseline(impact_service):
    """Test savings calculation without baseline."""
    current_metrics = PeriodMetrics(
        total_bytes_processed=1000000000,
        total_slot_ms=5000000,
        execution_count=10,
    )

    savings = impact_service._calculate_savings(
        current_metrics=current_metrics,
        baseline_metrics=None,
        smart_tuning_usage_count=5,
        matching_executions_count=10,
    )

    # Without baseline, savings should be 0
    assert savings["bytes_saved_per_execution"] == 0
    assert savings["slot_ms_saved_per_execution"] == 0
    assert savings["total_bytes_saved"] == 0
    assert savings["total_slot_ms_saved"] == 0
    assert savings["dollars_saved"] == 0.0
    assert savings["usage_percentage"] == 50.0


def test_calculate_savings_with_baseline(impact_service):
    """Test savings calculation with baseline."""
    current_metrics = PeriodMetrics(
        total_bytes_processed=1000000000,
        total_slot_ms=5000000,
        execution_count=10,
    )

    baseline_metrics = PeriodMetrics(
        total_bytes_processed=2000000000,
        total_slot_ms=10000000,
        execution_count=10,
    )

    savings = impact_service._calculate_savings(
        current_metrics=current_metrics,
        baseline_metrics=baseline_metrics,
        smart_tuning_usage_count=5,
        matching_executions_count=10,
    )

    # Savings should be positive
    assert savings["bytes_saved_per_execution"] == 100000000
    assert savings["slot_ms_saved_per_execution"] == 500000
    assert savings["total_bytes_saved"] == 500000000
    assert savings["total_slot_ms_saved"] == 2500000
    assert savings["dollars_saved"] > 0
    assert savings["usage_percentage"] == 50.0


def test_determine_attribution_method(impact_service):
    """Test attribution method determination."""
    # Single MV
    assert impact_service._determine_attribution_method(1, 10) == "bytes_proportional"

    # Multiple MVs with usage
    assert impact_service._determine_attribution_method(3, 5) == "bytes_proportional"

    # No usage
    assert impact_service._determine_attribution_method(3, 0) == "unknown"


def test_calculate_aggregates(impact_service):
    """Test aggregate statistics calculation."""
    from bigquery_automv.models.impact_report import MVImpact
    from bigquery_automv.models.mv_artifact import MVStatus

    mv_impacts = [
        MVImpact(
            mv_name="automv_test1",
            source_query_hash="abc123",
            baseline_avg_bytes_processed=None,
            baseline_avg_slot_ms=None,
            baseline_execution_count=None,
            current_avg_bytes_processed=1000000000,
            current_avg_slot_ms=5000000,
            current_execution_count=10,
            bytes_saved_per_execution=0,
            slot_ms_saved_per_execution=0,
            total_bytes_saved=1000000000,
            total_slot_ms_saved=5000000,
            dollars_saved_on_demand_equiv=5.0,
            matching_executions_count=10,
            smart_tuning_usage_count=10,
            direct_query_count=0,
            usage_percentage=100.0,
            attribution_method="bytes_proportional",
            status=MVStatus.ACTIVE,
        ),
        MVImpact(
            mv_name="automv_test2",
            source_query_hash="def456",
            baseline_avg_bytes_processed=None,
            baseline_avg_slot_ms=None,
            baseline_execution_count=None,
            current_avg_bytes_processed=2000000000,
            current_avg_slot_ms=10000000,
            current_execution_count=20,
            bytes_saved_per_execution=0,
            slot_ms_saved_per_execution=0,
            total_bytes_saved=2000000000,
            total_slot_ms_saved=10000000,
            dollars_saved_on_demand_equiv=10.0,
            matching_executions_count=20,
            smart_tuning_usage_count=20,
            direct_query_count=0,
            usage_percentage=100.0,
            attribution_method="bytes_proportional",
            status=MVStatus.ACTIVE,
        ),
    ]

    aggregates = impact_service._calculate_aggregates(mv_impacts)

    assert aggregates["total_bytes_saved"] == 3000000000
    assert aggregates["total_slot_ms_saved"] == 15000000
    assert aggregates["total_dollars_saved"] == 15.0
    assert aggregates["total_queries_accelerated"] == 30
    assert aggregates["matching_executions_count"] == 30
