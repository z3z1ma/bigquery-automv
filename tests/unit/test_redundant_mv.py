"""Test detection of redundant/identity MVs."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bigquery_automv.models.query_candidate import QueryCandidate, TableReference
from bigquery_automv.services.mv_generator import MVGenerationError, MVGeneratorService
from bigquery_automv.services.smart_tuning import SmartTuningCheckResult


@pytest.fixture
def mock_bq_client():
    client = MagicMock()
    client.project_id = "test-project"
    client.region = "US"
    client.get_dataset_region = AsyncMock(return_value="US")
    client.table_exists = AsyncMock(return_value=False)
    return client


@pytest.fixture
def mock_smart_tuning():
    service = MagicMock()
    # default to eligible
    service.check_elibility = AsyncMock(
        return_value=SmartTuningCheckResult(
            query_hash="hash",
            eligible=True,
            recommended_mv_select=["col1", "col2"],
            recommended_mv_filters=[],  # No filters = Identity if no Group By
            recommended_mv_group_by=[],
        )
    )
    return service


class TestRedundantMVs:
    @pytest.mark.asyncio
    async def test_reject_identity_mv(self, mock_bq_client, mock_smart_tuning):
        """Test that an MV which is just SELECT cols FROM table is rejected."""
        service = MVGeneratorService(bq_client=mock_bq_client, smart_tuning_service=mock_smart_tuning)

        candidate = QueryCandidate(
            query_hash="test_hash",
            representative_query="SELECT col1, col2 FROM `p.d.t` WHERE col1 = 1",
            execution_count=10,
            bytes_billed_total=1000,
            total_bytes_processed=1000,
            slot_ms_total=1000,
            impact_score=100.0,
            dollar_cost_est_on_demand=1.0,
            impact_model_version="v1",
            rulebook_version="v1",
            first_seen=None,
            last_seen=None,
            referenced_tables=[TableReference("p", "d", "t", "US")],
            # Simulate predicate lifting that removed all filters
            locked_predicates=[],
            lifted_columns=["col1"],
            smart_tuning_eligible=True,
        )

        # Override eligibility result to match the "lifted" state (no filters)
        mock_smart_tuning.check_elibility.return_value = SmartTuningCheckResult(
            query_hash="test_hash",
            eligible=True,
            recommended_mv_select=["col1", "col2"],
            recommended_mv_filters=[],
            recommended_mv_group_by=[],
        )

        with pytest.raises(MVGenerationError) as exc:
            await service.generate_mv_artifact(candidate=candidate, target_dataset="d", target_project="p")

        assert "Identity MV" in str(exc.value) or "value" in str(exc.value) or "optimization" in str(exc.value)
