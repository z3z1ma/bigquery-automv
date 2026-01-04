"""Unit tests for AnalyzerService job refinement logic."""

from unittest.mock import MagicMock

import pytest

from bigquery_automv.services.analyzer import AnalyzerService
from bigquery_automv.services.sql_parser import SQLParser


@pytest.fixture
def analyzer_service():
    client = MagicMock()
    service = AnalyzerService(client=client)
    # Ensure parser is initialized
    service._parser = SQLParser()
    return service


class TestAnalyzerRefinement:
    def test_refine_identical_queries(self, analyzer_service):
        """Test queries with identical predicates are grouped with all locked."""
        jobs = [
            {"job_id": "1", "query": "SELECT * FROM t WHERE col1 = 1 AND date = '2023-01-01'"},
            {"job_id": "2", "query": "SELECT * FROM t WHERE col1 = 1 AND date = '2023-01-01'"},
        ]

        groups = analyzer_service._refine_job_group(jobs)

        assert len(groups) == 1
        locked, lifted, group_jobs = groups[0]

        # Both predicates should be locked
        assert len(locked) == 2
        assert any("col1 = 1" in p for p in locked)
        assert any("date = '2023-01-01'" in p for p in locked)
        assert len(lifted) == 0
        assert len(group_jobs) == 2

    def test_refine_differing_non_partition_predicates(self, analyzer_service):
        """Test queries with differing non-partition predicates have intersection locked and diff lifted."""
        jobs = [
            {"job_id": "1", "query": "SELECT * FROM t WHERE user_id = 1 AND date = '2023-01-01'"},
            {"job_id": "2", "query": "SELECT * FROM t WHERE user_id = 2 AND date = '2023-01-01'"},
        ]

        groups = analyzer_service._refine_job_group(jobs)

        assert len(groups) == 1
        locked, lifted, group_jobs = groups[0]

        # Date should be locked (partition heuristic)
        assert len(locked) == 1
        assert any("date = '2023-01-01'" in p for p in locked)

        # user_id should be lifted
        assert len(lifted) == 1
        assert "user_id" in lifted

    def test_refine_differing_partition_predicates(self, analyzer_service):
        """Test queries with differing partition predicates are split into groups."""
        jobs = [
            {"job_id": "1", "query": "SELECT * FROM t WHERE date = '2023-01-01'"},
            {"job_id": "2", "query": "SELECT * FROM t WHERE date = '2023-01-02'"},
        ]

        groups = analyzer_service._refine_job_group(jobs)

        assert len(groups) == 2
        # Group 1
        locked1, lifted1, jobs1 = groups[0] if "2023-01-01" in groups[0][0][0] else groups[1]
        assert "date = '2023-01-01'" in locked1[0]

        # Group 2
        locked2, lifted2, jobs2 = groups[1] if "2023-01-02" in groups[1][0][0] else groups[0]
        assert "date = '2023-01-02'" in locked2[0]

    def test_refine_with_parse_error(self, analyzer_service):
        """Test that parse errors result in a fallback group."""
        jobs = [
            {"job_id": "1", "query": "SELECT * FROM t WHERE date = '2023-01-01'"},
            {"job_id": "2", "query": "INVALID SQL SYNTAX"},
        ]

        groups = analyzer_service._refine_job_group(jobs)

        assert len(groups) == 2

        # Check for parse error group
        error_group = next(g for g in groups if not g[0] and not g[1])
        assert len(error_group[2]) == 1
        assert error_group[2][0]["job_id"] == "2"

        # Check valid group
        valid_group = next(g for g in groups if g[0])
        assert len(valid_group[2]) == 1
        assert valid_group[2][0]["job_id"] == "1"

    def test_partition_predicate_heuristic(self, analyzer_service):
        """Test partition predicate heuristic."""
        assert analyzer_service._is_partition_predicate("date = '2023-01-01'") is True
        assert analyzer_service._is_partition_predicate("timestamp > '2023-01-01'") is True
        assert analyzer_service._is_partition_predicate("_PARTITIONDATE = '2023-01-01'") is True
        assert analyzer_service._is_partition_predicate("user_id = 1") is False
