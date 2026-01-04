"""Shared pytest fixtures and configuration."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from bigquery_automv.lib.config import AnalysisConfig, ImpactScoringConfig
from bigquery_automv.models.query_candidate import QueryCandidate, TableReference
from bigquery_automv.services.bq_client import BigQueryClient


@pytest.fixture
def mock_bq_client():
    """Create a mock BigQuery client."""
    client = MagicMock(spec=BigQueryClient)
    client.project_id = "test-project"
    client.region = "region-us"
    client._client = MagicMock()
    client._client._connection = MagicMock()
    client._client._connection.credentials = MagicMock()
    client._client._connection.credentials.email = "test@example.com"

    # Async methods
    client.query_information_schema_jobs = AsyncMock()
    client.query_materialized_view_statistics = AsyncMock()
    client.get_dataset_region = AsyncMock(return_value="US")
    client.validate_region_match = AsyncMock()
    client.table_exists = AsyncMock(return_value=False)
    client.get_materialized_view = AsyncMock()
    client.create_materialized_view = AsyncMock()
    client.drop_materialized_view = AsyncMock()
    client.insert_metadata = AsyncMock()

    return client


@pytest.fixture
def sample_query_candidate():
    """Create a sample QueryCandidate for testing."""
    return QueryCandidate(
        query_hash="abc123def456",
        representative_query="SELECT user_id, COUNT(*) as cnt FROM `project.dataset.table` GROUP BY user_id",
        execution_count=100,
        bytes_billed_total=10000000000,  # 10GB
        total_bytes_processed=15000000000,  # 15GB
        slot_ms_total=5000000,
        impact_score=6.25,
        dollar_cost_est_on_demand=6.25,
        impact_model_version="v1.0",
        rulebook_version="v1.0",
        first_seen=datetime(2024, 1, 1),
        last_seen=datetime(2024, 1, 31),
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
def analysis_config():
    """Create default AnalysisConfig."""
    return AnalysisConfig(
        min_executions=10,
        min_bytes=1073741824,  # 1GB
        min_slot_ms=0,
        max_families=100,
        sample_size_k=20,
    )


@pytest.fixture
def impact_config():
    """Create default ImpactScoringConfig."""
    return ImpactScoringConfig(
        price_per_tib=6.25,
        slot_weight=0.25,
        slot_ms_per_tib_equivalent=3.6e9,
    )


@pytest.fixture
def sample_job_rows():
    """Sample job rows from INFORMATION_SCHEMA.JOBS."""
    return [
        {
            "job_id": "job1",
            "query": "SELECT user_id, COUNT(*) FROM `project.dataset.table` GROUP BY user_id",
            "normalized_literals": "abc123",
            "total_bytes_billed": 1000000000,
            "total_bytes_processed": 1500000000,
            "total_slot_ms": 500000,
            "creation_time": datetime(2024, 1, 15, 12, 0, 0),
            "statement_type": "SELECT",
            "referenced_tables": [
                {"project_id": "project", "dataset_id": "dataset", "table_id": "table", "region": "US"}
            ],
        },
        {
            "job_id": "job2",
            "query": "SELECT user_id, COUNT(*) FROM `project.dataset.table` GROUP BY user_id",
            "normalized_literals": "abc123",
            "total_bytes_billed": 2000000000,
            "total_bytes_processed": 3000000000,
            "total_slot_ms": 1000000,
            "creation_time": datetime(2024, 1, 16, 12, 0, 0),
            "statement_type": "SELECT",
            "referenced_tables": [
                {"project_id": "project", "dataset_id": "dataset", "table_id": "table", "region": "US"}
            ],
        },
    ]
