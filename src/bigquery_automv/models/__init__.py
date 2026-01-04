"""Data models for BigQuery materialized view automation.

This module provides the core data structures used throughout the
bigquery-automv system for representing query candidates, materialized
views, cost analysis, and impact reporting.
"""

from bigquery_automv.models.cost_analysis import CostAnalysisResult
from bigquery_automv.models.impact_report import ImpactReport, MVImpact
from bigquery_automv.models.mv_artifact import MaterializedViewArtifact, MVStatus, SmartTuningCheckResult
from bigquery_automv.models.query_candidate import QueryCandidate, TableReference

__all__ = [
    # Query candidates and table references
    "QueryCandidate",
    "TableReference",
    # Smart Tuning and materialized views
    "SmartTuningCheckResult",
    "MaterializedViewArtifact",
    "MVStatus",
    # Cost analysis
    "CostAnalysisResult",
    # Impact reporting
    "ImpactReport",
    "MVImpact",
]
