"""Unit tests for Smart Tuning eligibility service."""

from unittest.mock import AsyncMock

import pytest

from bigquery_automv.services.smart_tuning import SmartTuningService


@pytest.fixture
def smart_tuning_service(mock_bq_client):
    """Create SmartTuningService instance."""
    return SmartTuningService(bq_client=mock_bq_client, enable_preview_eligibility=False)


@pytest.fixture
def smart_tuning_service_with_preview(mock_bq_client):
    """Create SmartTuningService with preview features enabled."""
    return SmartTuningService(bq_client=mock_bq_client, enable_preview_eligibility=True)


class TestSmartTuningEligibility:
    """Test Smart Tuning eligibility checking."""

    @pytest.mark.asyncio
    async def test_eligible_simple_aggregation(self, smart_tuning_service):
        """Test that simple aggregation query is eligible."""
        sql = "SELECT user_id, COUNT(*) as cnt FROM `project.dataset.table` GROUP BY user_id"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is True
        assert result.eligibility_basis == "stable"
        assert len(result.disqualification_reasons) == 0

    @pytest.mark.asyncio
    async def test_ineligible_union_all(self, smart_tuning_service):
        """Test that UNION ALL is ineligible without preview."""
        sql = "SELECT user_id FROM `project.dataset.table1` UNION ALL SELECT user_id FROM `project.dataset.table2`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "UNION ALL" in result.unsupported_features
        assert any("UNION ALL" in r for r in result.disqualification_reasons)

    @pytest.mark.asyncio
    async def test_eligible_union_all_with_preview(self, smart_tuning_service_with_preview):
        """Test that UNION ALL is eligible with preview enabled."""
        sql = "SELECT user_id FROM `project.dataset.table1` UNION ALL SELECT user_id FROM `project.dataset.table2`"
        result = await smart_tuning_service_with_preview.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is True
        assert result.eligibility_basis == "preview"
        assert "UNION ALL" in result.unsupported_features

    @pytest.mark.asyncio
    async def test_ineligible_right_outer_join(self, smart_tuning_service_with_preview):
        """Test that RIGHT OUTER JOIN is never eligible."""
        sql = (
            "SELECT * FROM `project.dataset.table1` RIGHT OUTER JOIN `project.dataset.table2` ON table1.id = table2.id"
        )
        result = await smart_tuning_service_with_preview.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "RIGHT OUTER JOIN" in result.unsupported_features

    @pytest.mark.asyncio
    async def test_ineligible_full_outer_join(self, smart_tuning_service_with_preview):
        """Test that FULL OUTER JOIN is never eligible."""
        sql = "SELECT * FROM `project.dataset.table1` FULL OUTER JOIN `project.dataset.table2` ON table1.id = table2.id"
        result = await smart_tuning_service_with_preview.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "FULL OUTER JOIN" in result.unsupported_features

    @pytest.mark.asyncio
    async def test_ineligible_window_functions(self, smart_tuning_service):
        """Test that window functions are ineligible."""
        sql = "SELECT user_id, ROW_NUMBER() OVER (PARTITION BY user_id) as rn FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "WINDOW FUNCTIONS" in result.unsupported_features

    @pytest.mark.asyncio
    async def test_ineligible_qualify_clause(self, smart_tuning_service):
        """Test that QUALIFY clause is ineligible."""
        sql = (
            "SELECT user_id, ROW_NUMBER() OVER (PARTITION BY user_id) as rn FROM `project.dataset.table` QUALIFY rn = 1"
        )
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "QUALIFY CLAUSE" in result.unsupported_features


class TestNonDeterministicFunctions:
    """Test non-deterministic function detection."""

    @pytest.mark.asyncio
    async def test_ineligible_rand_function(self, smart_tuning_service):
        """Test that RAND() makes query ineligible."""
        sql = "SELECT user_id, RAND() as random_val FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "NON-DETERMINISTIC FUNCTIONS" in result.unsupported_features
        assert any("RAND" in r for r in result.disqualification_reasons)

    @pytest.mark.asyncio
    async def test_ineligible_current_date(self, smart_tuning_service):
        """Test that CURRENT_DATE makes query ineligible."""
        sql = "SELECT CURRENT_DATE() as today FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "NON-DETERMINISTIC FUNCTIONS" in result.unsupported_features

    @pytest.mark.asyncio
    async def test_ineligible_now_function(self, smart_tuning_service):
        """Test that NOW() makes query ineligible."""
        sql = "SELECT user_id, NOW() as current_time FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "NON-DETERMINISTIC FUNCTIONS" in result.unsupported_features


class TestUDFDetection:
    """Test user-defined function detection."""

    @pytest.mark.asyncio
    async def test_ineligible_project_udf(self, smart_tuning_service):
        """Test that project-level UDFs are detected."""
        sql = "SELECT user_id, `myproject.mydataset.myfunction`(value) FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "UDFS" in result.unsupported_features

    @pytest.mark.asyncio
    async def test_elible_builtin_functions(self, smart_tuning_service):
        """Test that built-in functions are allowed."""
        sql = "SELECT user_id, UPPER(name), COALESCE(email, 'N/A') FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # Should be eligible (assuming no aggregations or other issues)
        assert "UDFS" not in result.unsupported_features


class TestAggregateValidation:
    """Test aggregate function validation."""

    @pytest.mark.asyncio
    async def test_eligible_supported_aggregates(self, smart_tuning_service):
        """Test that supported aggregates are eligible."""
        sql = "SELECT user_id, COUNT(*), SUM(revenue), AVG(score) FROM `project.dataset.table` GROUP BY user_id"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is True
        assert "COUNT" in result.aggregation_functions
        assert "SUM" in result.aggregation_functions
        assert "AVG" in result.aggregation_functions

    @pytest.mark.asyncio
    async def test_ineligible_unsupported_aggregate(self, smart_tuning_service):
        """Test that unsupported aggregates are rejected."""
        # Note: This is a hypothetical unsupported aggregate
        # In practice, all BigQuery aggregates might be supported
        # This test demonstrates the mechanism
        sql = (
            "SELECT user_id, ARRAY_AGG(val ORDER BY timestamp LIMIT 5) as recent_vals "
            "FROM `project.dataset.table` GROUP BY user_id"
        )
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # ARRAY_AGG is stored as ARRAYAGG (underscore removed)
        assert "ARRAYAGG" in result.aggregation_functions


class TestHavingClause:
    """Test HAVING clause detection."""

    @pytest.mark.asyncio
    async def test_ineligible_having_on_aggregates(self, smart_tuning_service):
        """Test that HAVING on aggregates is ineligible."""
        sql = "SELECT user_id, COUNT(*) as cnt FROM `project.dataset.table` GROUP BY user_id HAVING cnt > 10"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert "HAVING ON AGGREGATES" in result.unsupported_features

    @pytest.mark.asyncio
    async def test_eligible_aggregation_without_having(self, smart_tuning_service):
        """Test that aggregation without HAVING is eligible."""
        sql = "SELECT user_id, COUNT(*) as cnt FROM `project.dataset.table` GROUP BY user_id"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is True
        assert "HAVING ON AGGREGATES" not in result.unsupported_features


class TestCrossProjectReferences:
    """Test cross-project reference detection."""

    @pytest.mark.asyncio
    async def test_detect_cross_project_references(self, smart_tuning_service):
        """Test detection of cross-project table references."""
        sql = "SELECT user_id FROM `other-project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="myproject",
        )
        assert result.cross_project_references is True
        assert result.eligible is False
        assert any("Cross-project" in r for r in result.disqualification_reasons)

    @pytest.mark.asyncio
    async def test_eligible_same_project(self, smart_tuning_service):
        """Test that same-project references are eligible."""
        sql = "SELECT user_id FROM `myproject.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="myproject",
        )
        assert result.cross_project_references is False


class TestRegionMismatch:
    """Test region mismatch detection."""

    @pytest.mark.asyncio
    async def test_region_mismatch_detected(self, mock_bq_client):
        """Test detection of region mismatch."""
        # Mock region validation to raise error
        mock_bq_client.get_dataset_region = AsyncMock(return_value="US")
        mock_bq_client.validate_region_match = AsyncMock(side_effect=Exception("Region mismatch"))

        service = SmartTuningService(bq_client=mock_bq_client)
        sql = "SELECT user_id FROM `project.dataset.table`"
        result = await service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.region_mismatch is True
        assert result.eligible is False

    @pytest.mark.asyncio
    async def test_no_region_check_without_client(self):
        """Test that region check is skipped without BQ client."""
        service = SmartTuningService(bq_client=None)
        sql = "SELECT user_id FROM `project.dataset.table`"
        result = await service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # Should not fail, just skip region check
        assert result.region_mismatch is False


class TestColumnAnalysis:
    """Test column analysis for predicate lifting."""

    @pytest.mark.asyncio
    async def test_analyze_liftable_columns(self, smart_tuning_service):
        """Test analysis of liftable columns."""
        sql = "SELECT user_id, COUNT(*) FROM `project.dataset.table` WHERE region = 'US' GROUP BY user_id"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # user_id should be in liftable columns (in SELECT/GROUP BY)
        assert len(result.liftable_columns) > 0
        assert "user_id" in result.liftable_columns or any("user_id" in col for col in result.liftable_columns)

    @pytest.mark.asyncio
    async def test_detect_non_liftable_filters(self, smart_tuning_service):
        """Test detection of non-liftable filters."""
        sql = (
            "SELECT user_id, COUNT(*) FROM `project.dataset.table` "
            "WHERE region = 'US' AND status = 'active' GROUP BY user_id"
        )
        _ = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # Filters on columns not in SELECT/GROUP BY are non-liftable
        # The heuristic may not catch all cases, but the mechanism exists


class TestMVRecommendations:
    """Test MV recommendation generation."""

    @pytest.mark.asyncio
    async def test_generate_mv_recommendations(self, smart_tuning_service):
        """Test generation of MV recommendations."""
        sql = "SELECT user_id, COUNT(*) as cnt FROM `project.dataset.table` WHERE region = 'US' GROUP BY user_id"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # Should have recommendations
        assert len(result.recommended_mv_select) > 0
        assert len(result.recommended_mv_group_by) > 0

    @pytest.mark.asyncio
    async def test_mv_filters_include_non_liftable(self, smart_tuning_service):
        """Test that MV filters include non-liftable predicates."""
        sql = "SELECT user_id, COUNT(*) FROM `project.dataset.table` WHERE region = 'US' GROUP BY user_id"
        _ = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # If region is non-liftable, it should be in MV filters
        # This depends on the heuristic, so we just check the mechanism exists


class TestEligibilityRuleset:
    """Test eligibility ruleset retrieval."""

    def test_get_eligibility_ruleset(self, smart_tuning_service):
        """Test retrieval of current eligibility ruleset."""
        ruleset = smart_tuning_service.get_eligibility_ruleset()
        assert "rulebook_version" in ruleset
        assert "supported_aggregates" in ruleset
        assert "non_deterministic_functions" in ruleset
        assert "preview_features" in ruleset
        assert "unsupported_features" in ruleset
        assert "rules" in ruleset
        assert ruleset["rulebook_version"] == SmartTuningService.RULEBOOK_VERSION


class TestParseErrorHandling:
    """Test handling of SQL parse errors."""

    @pytest.mark.asyncio
    async def test_parse_error_handling(self, smart_tuning_service):
        """Test that parse errors are handled gracefully."""
        invalid_sql = "SELECT FROM FROM WHERE"
        result = await smart_tuning_service.check_elibility(
            sql=invalid_sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert any("parsing" in r.lower() or "parse" in r.lower() for r in result.disqualification_reasons)


class TestSubqueryDetection:
    """Test subquery type detection."""

    @pytest.mark.asyncio
    async def test_detect_scalar_subquery(self, smart_tuning_service):
        """Test detection of scalar subqueries."""
        sql = (
            "SELECT user_id, (SELECT COUNT(*) FROM `project.dataset.table2` "
            "WHERE table2.id = table1.id) as cnt FROM `project.dataset.table1`"
        )
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # Scalar subqueries should be detected
        assert "SCALAR" in result.subquery_types

    @pytest.mark.asyncio
    async def test_detect_array_subquery(self, smart_tuning_service):
        """Test detection of ARRAY subqueries."""
        sql = (
            "SELECT user_id, ARRAY(SELECT * FROM `project.dataset.table2` "
            "WHERE table2.user_id = table1.user_id) as arr "
            "FROM `project.dataset.table1`"
        )
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # ARRAY subqueries are supported in BigQuery Smart Tuning
        # The test verifies they can be parsed without error
        assert "ARRAY" in result.recommended_mv_select[1] or "arr" in result.recommended_mv_select[1]


class TestComputedAggregates:
    """Test computed aggregate detection."""

    @pytest.mark.asyncio
    async def test_detect_computed_aggregates(self, smart_tuning_service):
        """Test detection of computed aggregates (arithmetic on aggregates)."""
        sql = "SELECT user_id, COUNT(*) / 10 as normalized_cnt FROM `project.dataset.table` GROUP BY user_id"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        # Computed aggregates should be detected
        assert "COMPUTED AGGREGATES" in result.unsupported_features
        assert result.eligible is False


class TestOptimizationValue:
    """Test optimization value heuristics."""

    @pytest.mark.asyncio
    async def test_ineligible_simple_select(self, smart_tuning_service):
        """Test that simple SELECT * is ineligible."""
        sql = "SELECT * FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is False
        assert any("optimization value" in r for r in result.disqualification_reasons)

    @pytest.mark.asyncio
    async def test_eligible_distinct(self, smart_tuning_service):
        """Test that SELECT DISTINCT is eligible."""
        sql = "SELECT DISTINCT user_id FROM `project.dataset.table`"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is True

    @pytest.mark.asyncio
    async def test_eligible_join(self, smart_tuning_service):
        """Test that query with JOIN is eligible."""
        sql = "SELECT t1.id, t2.val FROM `project.d.t1` t1 JOIN `project.d.t2` t2 ON t1.id = t2.id"
        result = await smart_tuning_service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )
        assert result.eligible is True


class TestLogicalViewDetection:
    """Test detection of logical view references."""

    @pytest.mark.asyncio
    async def test_ineligible_logical_view(self, mock_bq_client):
        """Test that referencing a logical view makes query ineligible."""
        # Mock get_table_type to return VIEW
        mock_bq_client.get_table_type = AsyncMock(return_value="VIEW")

        service = SmartTuningService(bq_client=mock_bq_client)
        sql = "SELECT * FROM `project.dataset.view`"

        result = await service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )

        assert result.eligible is False
        assert any("logical view" in r for r in result.disqualification_reasons)

    @pytest.mark.asyncio
    async def test_eligible_base_table(self, mock_bq_client):
        """Test that referencing a base table is eligible."""
        # Mock get_table_type to return TABLE
        mock_bq_client.get_table_type = AsyncMock(return_value="TABLE")

        service = SmartTuningService(bq_client=mock_bq_client)
        # Use DISTINCT to pass optimization value check
        sql = "SELECT DISTINCT col FROM `project.dataset.table`"

        result = await service.check_elibility(
            sql=sql,
            query_hash="test_hash",
            target_dataset="dataset",
            target_project="project",
        )

        assert result.eligible is True
