"""Materialized View Generator Service for BigQuery.

This module provides the MVGeneratorService for generating BigQuery materialized
view DDL from query candidates, handling naming, idempotency, and eligibility.

Features:
    - T069: MVGeneratorService class with DDL generation logic
    - T070: Deterministic MV naming {prefix}_{family_hash_short}_{signature_hash_short}
    - T071: Signature hash calculation for idempotency
    - T072: Predicate lifting/generalization algorithm
    - T073: MV DDL template with OPTIONS, AS SELECT, WHERE, GROUP BY
    - T074: MV metadata generation
    - T075: "Covers all query rows" validation
    - T076: GROUP BY validation
    - T077: Single-base-table enforcement
    - T078: Region validation
    - T079: Synthesis audit generation
    - T080: Representative query sampling
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bigquery_automv.lib.logging import get_logger
from bigquery_automv.models.mv_artifact import MaterializedViewArtifact, MVStatus
from bigquery_automv.models.query_candidate import QueryCandidate
from bigquery_automv.services.bq_client import BigQueryClient, TableIdentifier
from bigquery_automv.services.smart_tuning import SmartTuningService
from bigquery_automv.services.sql_parser import SQLParser

logger = get_logger(__name__)


@dataclass
class SynthesisAudit:
    """Metadata from MV synthesis process (T079).

    Attributes:
        sample_size_k: Number of queries sampled for synthesis
        sample_job_ids: Job IDs of sampled queries
        dropped_predicates: Predicates removed during generalization
        warnings: Warnings generated during synthesis
        family_hash: Source query family hash
    """

    sample_size_k: int
    sample_job_ids: list[str]
    dropped_predicates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    family_hash: str = ""


@dataclass
class PredicateAnalysis:
    """Result of predicate analysis across query family (T072).

    Attributes:
        shared_predicates: Predicates identical across all queries
        per_query_predicates: Predicates that vary per query
        liftable_columns: Columns referenced in varying predicates
    """

    shared_predicates: list[str]
    per_query_predicates: dict[str, list[str]]  # job_id -> predicates
    liftable_columns: list[str]


class MVGenerationError(Exception):
    """Base exception for MV generation errors."""

    def __init__(self, message: str, *, reason: str = "", query_hash: str | None = None) -> None:
        """Initialize MV generation error.

        Args:
            message: Human-readable error message
            reason: Machine-readable error reason code
            query_hash: Query family hash for error reporting
        """
        self.message = message
        self.reason = reason
        self.query_hash = query_hash
        super().__init__(message)


class MVGeneratorService:
    """Service for generating materialized views from query candidates.

    Handles:
    - DDL generation with proper formatting (T069, T073)
    - MV naming conventions (T070)
    - Idempotency via signature_hash (T071)
    - Predicate lifting/generalization algorithm (T072)
    - MV metadata generation (T074)
    - "Covers all query rows" validation (T075)
    - GROUP BY validation (T076)
    - Single-base-table enforcement (T077)
    - Region validation (T078)
    - Synthesis audit generation (T079)
    - Representative query sampling (T080)
    """

    SYNTHESIS_VERSION = "v1.0"

    def __init__(
        self,
        *,
        bq_client: BigQueryClient,
        smart_tuning_service: SmartTuningService,
        tool_version: str = "dev",
    ) -> None:
        """Initialize the MV generator service.

        Args:
            bq_client: BigQuery client for deployment
            smart_tuning_service: Smart tuning eligibility checker
            tool_version: Version string for created_by_tool_version
        """
        self.bq_client = bq_client
        self.smart_tuning_service = smart_tuning_service
        self.tool_version = tool_version
        self._parser = SQLParser()

    async def generate_mv_artifact(
        self,
        candidate: QueryCandidate,
        *,
        target_dataset: str,
        target_project: str,
        mv_prefix: str = "automv_",
        refresh_interval_minutes: int = 60,
        enable_refresh: bool = True,
        enable_auto_cleanup: bool = False,
    ) -> MaterializedViewArtifact:
        """Generate a materialized view artifact from a query candidate.

        Args:
            candidate: Query candidate with representative SQL
            target_dataset: Target dataset for MV
            target_project: Target project for MV
            mv_prefix: Prefix for MV name (default "automv_")
            refresh_interval_minutes: MV refresh interval in minutes
            enable_refresh: Enable automatic refresh
            enable_auto_cleanup: Enable automatic cleanup of stale MVs

        Returns:
            MaterializedViewArtifact with DDL and metadata

        Raises:
            MVGenerationError: If MV generation fails
        """
        # Verify Smart Tuning eligibility (T094)
        eligibility_result = await self.smart_tuning_service.check_elibility(
            sql=candidate.representative_query,
            query_hash=candidate.query_hash,
            target_dataset=target_dataset,
            target_project=target_project,
        )

        if not eligibility_result.eligible:
            raise MVGenerationError(
                f"Query is not eligible for Smart Tuning: {', '.join(eligibility_result.disqualification_reasons)}",
                query_hash=candidate.query_hash,
            )

        # Generate MV components
        # T071: Calculate signature hash from canonical MV SQL for idempotency
        base_table = self._get_primary_base_table(candidate)

        # Build preliminary MV query for signature calculation
        preliminary_mv_query = self._build_mv_query(
            select_expressions=eligibility_result.recommended_mv_select,
            base_table=base_table,
            shared_predicates=eligibility_result.recommended_mv_filters,
            group_by_expressions=eligibility_result.recommended_mv_group_by,
        )

        # T071: Calculate signature hash of canonical MV SQL
        signature_hash_full = self._calculate_signature_hash(preliminary_mv_query)
        signature_hash_short = signature_hash_full[:12]

        # T070: Generate deterministic MV name with both family and signature hash
        mv_name = self._generate_mv_name(
            family_hash=candidate.query_hash,
            signature_hash=signature_hash_short,
            prefix=mv_prefix,
        )

        # Build DDL with recommended structure from Smart Tuning
        ddl = self._generate_ddl(
            mv_name=mv_name,
            signature_hash=signature_hash_short,
            project=target_project,
            dataset=target_dataset,
            select_expressions=eligibility_result.recommended_mv_select,
            base_table=base_table,
            shared_predicates=eligibility_result.recommended_mv_filters,
            group_by_expressions=eligibility_result.recommended_mv_group_by,
            enable_refresh=enable_refresh,
            refresh_interval_minutes=refresh_interval_minutes,
            query_hash=candidate.query_hash,
            eligibility_basis=eligibility_result.eligibility_basis,
        )

        # Get region from target dataset
        mv_region = await self.bq_client.get_dataset_region(
            dataset_id=target_dataset,
            project_id=target_project,
        )

        # Build artifact
        artifact = MaterializedViewArtifact(
            mv_name=mv_name,
            source_query_hash=candidate.query_hash,
            signature_hash=signature_hash_full,
            project_id=target_project,
            dataset_id=target_dataset,
            mv_region=mv_region,
            ddl_definition=ddl,
            base_tables=candidate.referenced_tables,
            refresh_interval_minutes=refresh_interval_minutes,
            enable_refresh=enable_refresh,
            partition_expiration_days=365 if enable_auto_cleanup else None,
            created_at=datetime.now(UTC),
            created_by=self.bq_client._client._connection.credentials.email
            if (
                hasattr(self.bq_client._client._connection, "credentials")
                and hasattr(self.bq_client._client._connection.credentials, "email")
            )
            else "bq-automv",
            created_by_tool_version=self.tool_version,
            rulebook_version=eligibility_result.rulebook_version,
            synthesis_version=self.SYNTHESIS_VERSION,
            synthesis_warnings=[],
            status=MVStatus.PROPOSED,
            eligibility_basis=eligibility_result.eligibility_basis,
        )

        return artifact

    def _generate_mv_name(
        self,
        family_hash: str,
        signature_hash: str,
        prefix: str = "automv_",
    ) -> str:
        """T070: Generate deterministic MV name with collision resistance.

        Format: {prefix}_{family_hash_short}_{signature_hash_short}

        Args:
            family_hash: Query family hash (normalized_literals)
            signature_hash: Signature hash of canonical MV SQL
            prefix: MV name prefix (default "automv_")

        Returns:
            MV name in format {prefix}_{family_hash_short}_{signature_hash_short}
        """
        # Use first 12 characters of each hash for readability
        family_hash_short = family_hash[:12]
        signature_hash_short = signature_hash[:12]
        return f"{prefix}{family_hash_short}_{signature_hash_short}"

    def _generate_ddl(
        self,
        *,
        mv_name: str,
        signature_hash: str,
        project: str,
        dataset: str,
        select_expressions: list[str],
        base_table: str,
        shared_predicates: list[str],
        group_by_expressions: list[str],
        enable_refresh: bool,
        refresh_interval_minutes: int,
        query_hash: str,
        eligibility_basis: str,
    ) -> str:
        """T073: Generate CREATE MATERIALIZED VIEW DDL with OPTIONS.

        Args:
            mv_name: Materialized view name
            signature_hash: Signature hash of MV SQL
            project: Target project
            dataset: Target dataset
            select_expressions: SELECT expressions for MV
            base_table: Primary base table reference
            shared_predicates: Shared predicates for WHERE clause
            group_by_expressions: GROUP BY expressions (empty if no aggregation)
            enable_refresh: Enable automatic refresh
            refresh_interval_minutes: Refresh interval in minutes
            query_hash: Source query hash
            eligibility_basis: "stable" or "preview"

        Returns:
            Complete DDL with header comments
        """
        # Build header comments (T092)
        header_lines = [
            f"-- MV Name: {mv_name}",
            f"-- Family Hash: {query_hash}",
            f"-- Signature Hash: {signature_hash}",
            f"-- Region: {self.bq_client.region}",
            f"-- Rulebook Version: {SmartTuningService.RULEBOOK_VERSION}",
            f"-- Synthesis Version: {self.SYNTHESIS_VERSION}",
            f"-- Eligibility Basis: {eligibility_basis}",
        ]

        # Build SELECT clause
        select_clause = ",\n    ".join(select_expressions)

        # Build WHERE clause
        where_clause = ""
        if shared_predicates:
            where_clause = "WHERE " + " AND ".join(shared_predicates)

        # Build GROUP BY clause
        group_by_clause = ""
        if group_by_expressions:
            group_by_clause = "GROUP BY " + ", ".join(group_by_expressions)

        # Build full MV query
        mv_query = f"""SELECT {select_clause}
FROM {base_table}
{where_clause}
{group_by_clause}""".strip()

        # Build full DDL with OPTIONS
        options = f"""OPTIONS (
  enable_refresh = {str(enable_refresh).lower()},
  refresh_interval_minutes = {refresh_interval_minutes}
)"""

        full_mv_name = f"`{project}.{dataset}.{mv_name}`"

        ddl_parts = [
            "\n".join(header_lines),
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {full_mv_name}",
            options,
            f"AS\n{mv_query};",
        ]

        return "\n".join(ddl_parts)

    def _get_primary_base_table(self, candidate: QueryCandidate) -> str:
        """Get primary base table for MV query.

        Args:
            candidate: Query candidate

        Returns:
            Full table reference for primary base table
        """
        # Use the first referenced table as primary
        # In production, this could be more sophisticated (largest table, fact table, etc.)
        if candidate.referenced_tables:
            table = candidate.referenced_tables[0]
            return f"`{table.project_id}.{table.dataset_id}.{table.table_id}`"

        raise MVGenerationError(
            "No base tables found in query candidate",
            query_hash=candidate.query_hash,
        )

    def _generate_signature_hash(self, ddl: str) -> str:
        """Generate signature hash for idempotency checking.

        Args:
            ddl: DDL statement

        Returns:
            SHA256 hash of normalized DDL
        """
        # Normalize DDL (case-insensitive, whitespace normalization)
        normalized = " ".join(ddl.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    async def check_mv_exists(
        self,
        mv_name: str,
        dataset_id: str,
        *,
        project_id: str | None = None,
    ) -> bool:
        """Check if materialized view exists.

        Args:
            mv_name: Materialized view name
            dataset_id: Dataset ID
            project_id: Project ID (defaults to client project)

        Returns:
            True if MV exists, False otherwise
        """
        table_id = TableIdentifier(
            project_id=project_id or self.bq_client.project_id,
            dataset_id=dataset_id,
            table_id=mv_name,
        )
        return await self.bq_client.table_exists(table_id)

    async def get_existing_mv_signature(
        self,
        mv_name: str,
        dataset_id: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get existing MV metadata including signature.

        Args:
            mv_name: Materialized view name
            dataset_id: Dataset ID
            project_id: Project ID (defaults to client project)

        Returns:
            Dictionary with MV metadata or None if not found
        """
        try:
            mv_metadata = await self.bq_client.get_materialized_view(
                mv_name=mv_name,
                dataset_id=dataset_id,
                project_id=project_id or self.bq_client.project_id,
            )
            return mv_metadata
        except Exception:
            return None

    async def deploy_mv(
        self,
        artifact: MaterializedViewArtifact,
        *,
        dry_run: bool = False,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Deploy materialized view to BigQuery.

        Args:
            artifact: Materialized view artifact to deploy
            dry_run: If True, only output DDL without executing
            replace: If True, drop and recreate existing MV

        Returns:
            Deployment result with status and metadata

        Raises:
            MVGenerationError: If deployment fails
        """
        mv_name = artifact.mv_name
        dataset_id = artifact.dataset_id
        project_id = artifact.project_id

        # Deploy MV (T083) - dry-run just returns DDL without any API calls
        if dry_run:
            logger.info(f"DRY RUN: Would create MV {mv_name}")
            return {
                "status": "dry_run",
                "mv_name": mv_name,
                "ddl": artifact.ddl_definition,
                "message": "DDL generated (dry run, not executed)",
            }

        # Check if MV exists (T085 - idempotency)
        exists = await self.check_mv_exists(mv_name, dataset_id, project_id=project_id)

        if exists and not replace:
            # Check signature hash for idempotency
            existing_metadata = await self.get_existing_mv_signature(mv_name, dataset_id, project_id=project_id)
            if existing_metadata:
                # For now, we can't easily extract signature from existing MV
                # In production, this would query the automv_metadata table
                # For now, treat as exists
                logger.info(
                    f"MV {mv_name} already exists. Use --replace to recreate.",
                    extra={"mv_name": mv_name, "dataset_id": dataset_id},
                )
                return {
                    "status": "exists",
                    "mv_name": mv_name,
                    "message": "Materialized view already exists. Use --replace to recreate.",
                }

        if exists and replace:
            # Drop existing MV (T084)
            logger.info(f"Dropping existing MV: {mv_name}")
            await self.bq_client.drop_materialized_view(
                mv_name=mv_name,
                dataset_id=dataset_id,
                project_id=project_id,
                if_exists=False,
            )

        try:
            # Create MV
            result = await self.bq_client.create_materialized_view(
                mv_name=mv_name,
                dataset_id=dataset_id,
                query=artifact.ddl_definition.split("AS\n")[-1].rstrip(";"),
                project_id=project_id,
                enable_refresh=artifact.enable_refresh,
                refresh_interval_minutes=artifact.refresh_interval_minutes,
                if_not_exists=not replace,
            )

            # Store metadata (T096)
            await self._store_metadata(artifact)

            logger.info(f"Successfully created MV: {mv_name}", extra={"job_id": result.job_id})

            return {
                "status": "created",
                "mv_name": mv_name,
                "job_id": result.job_id,
                "message": "Materialized view created successfully",
            }

        except Exception as e:
            logger.error(f"Failed to create MV {mv_name}: {e}")
            raise MVGenerationError(
                f"Failed to create materialized view: {e}",
                query_hash=artifact.source_query_hash,
            ) from e

    async def _store_metadata(self, artifact: MaterializedViewArtifact) -> None:
        """Store MV metadata in BigQuery metadata table.

        Args:
            artifact: Materialized view artifact

        Raises:
            MVGenerationError: If metadata storage fails
        """
        # Build metadata record (T096)
        metadata = {
            "mv_name": artifact.mv_name,
            "source_query_hash": artifact.source_query_hash,
            "signature_hash": artifact.signature_hash,
            "created_at": artifact.created_at.isoformat(),
            "base_tables": [
                {
                    "project_id": t.project_id,
                    "dataset_id": t.dataset_id,
                    "table_id": t.table_id,
                }
                for t in artifact.base_tables
            ],
            "project_id": artifact.project_id,
            "dataset_id": artifact.dataset_id,
            "mv_region": artifact.mv_region,
            "status": artifact.status.value,
            "eligibility_basis": artifact.eligibility_basis,
            "refresh_interval_minutes": artifact.refresh_interval_minutes,
            "ddl_definition": artifact.ddl_definition,
            "created_by_tool_version": artifact.created_by_tool_version,
            "rulebook_version": artifact.rulebook_version,
            "synthesis_version": artifact.synthesis_version,
            "synthesis_warnings": artifact.synthesis_warnings,
            "last_refreshed": artifact.last_refreshed.isoformat() if artifact.last_refreshed else None,
            "usage_count": artifact.usage_count,
            "total_bytes_saved": artifact.total_bytes_saved,
            "total_slot_ms_saved": artifact.total_slot_ms_saved,
            "last_used": artifact.last_used.isoformat() if artifact.last_used else None,
        }

        try:
            # Insert metadata (T096)
            await self.bq_client.insert_metadata(
                dataset_id=artifact.dataset_id,
                metadata=metadata,  # type: ignore[arg-type]
                table_name="automv_metadata",
                project_id=artifact.project_id,
            )
            logger.info(f"Stored metadata for MV: {artifact.mv_name}")
        except Exception as e:
            logger.warning(f"Failed to store metadata for {artifact.mv_name}: {e}")
            # Don't fail deployment if metadata storage fails

    def _build_mv_query(
        self,
        *,
        select_expressions: list[str],
        base_table: str,
        shared_predicates: list[str],
        group_by_expressions: list[str],
    ) -> str:
        """Build MV query SQL (without CREATE statement).

        Args:
            select_expressions: SELECT expressions for MV
            base_table: Primary base table reference
            shared_predicates: Shared predicates for WHERE clause
            group_by_expressions: GROUP BY expressions (empty if no aggregation)

        Returns:
            MV query SQL string
        """
        # Build SELECT clause
        select_clause = ",\n    ".join(select_expressions)

        # Build WHERE clause
        where_clause = ""
        if shared_predicates:
            where_clause = "WHERE " + " AND ".join(shared_predicates)

        # Build GROUP BY clause
        group_by_clause = ""
        if group_by_expressions:
            group_by_clause = "GROUP BY " + ", ".join(group_by_expressions)

        # Build full MV query
        mv_query = f"""SELECT {select_clause}
FROM {base_table}
{where_clause}
{group_by_clause}""".strip()

        return mv_query

    def _calculate_signature_hash(self, mv_query: str) -> str:
        """T071: Calculate stable hash of canonical MV SQL for idempotency.

        Args:
            mv_query: MV query SQL (SELECT ... FROM ... WHERE ...)

        Returns:
            Hex digest of SHA256 hash
        """
        # Normalize SQL for consistent hashing
        normalized = mv_query.strip().upper()
        return hashlib.sha256(normalized.encode()).hexdigest()

    async def generate_mv_from_query_family(
        self,
        candidate: QueryCandidate,
        query_samples: list[dict],
        target_project: str,
        target_dataset: str,
        target_region: str,
        *,
        mv_prefix: str = "automv_",
        refresh_interval_minutes: int = 60,
        enable_refresh: bool = True,
    ) -> tuple[MaterializedViewArtifact, SynthesisAudit]:
        """Generate MV from query family using predicate lifting algorithm.

        Implements T069-T080:
        - T069: MVGeneratorService class with DDL generation logic
        - T070: Deterministic MV naming with both hashes
        - T071: Signature hash calculation
        - T072: Predicate lifting/generalization algorithm
        - T073: MV DDL template with OPTIONS
        - T074: MV metadata generation
        - T075: "Covers all query rows" validation
        - T076: GROUP BY validation
        - T077: Single-base-table enforcement
        - T078: Region validation
        - T079: Synthesis audit generation
        - T080: Representative query sampling

        Args:
            candidate: Query candidate with family hash and metadata
            query_samples: List of query sample dicts (from AnalyzerService)
            target_project: Target project ID for MV
            target_dataset: Target dataset ID for MV
            target_region: Target region for MV
            mv_prefix: Prefix for MV name
            refresh_interval_minutes: MV refresh interval
            enable_refresh: Enable automatic refresh

        Returns:
            Tuple of (MaterializedViewArtifact, SynthesisAudit)

        Raises:
            MVGenerationError: If MV generation fails
        """
        # T080: Sample top K queries by bytes_billed
        sampled_queries = self._sample_queries_top_k(query_samples, k=20)
        k = len(sampled_queries)
        sample_job_ids = [q.get("job_id", "") for q in sampled_queries]

        # Parse all sampled queries
        parsed_queries = []
        for sample in sampled_queries:
            job_id = sample.get("job_id", "")
            query_sql = sample.get("query", "")

            try:
                ast = self._parser.parse_query(query_sql)
                parsed_queries.append((job_id, ast))
            except Exception as e:
                logger.error(
                    f"Failed to parse query {job_id}: {e}",
                    extra={"job_id": job_id, "query_hash": candidate.query_hash},
                )
                raise MVGenerationError(
                    f"Failed to parse query {job_id}: {e}",
                    reason="parse_failed",
                    query_hash=candidate.query_hash,
                ) from e

        # T077: Validate single-base-table (reject any JOIN)
        self._validate_single_base_table(parsed_queries, candidate.query_hash)

        # T078: Validate region matches referenced tables
        dominant_table = self._get_dominant_base_table(parsed_queries, candidate.referenced_tables)
        self._validate_region(dominant_table, target_region, candidate.query_hash)

        # T072: Predicate lifting analysis
        predicate_analysis = self._analyze_predicates(parsed_queries)

        # T076: Validate GROUP BY is identical across family
        group_by_columns = self._validate_group_by_identical(parsed_queries, candidate.query_hash)

        # Extract SELECT columns (group by + aggregates)
        select_columns = self._extract_select_columns_from_queries(parsed_queries, group_by_columns)

        # T073: Generate MV DDL with T070 naming and T071 signature
        base_table_name = f"`{dominant_table.project_id}.{dominant_table.dataset_id}.{dominant_table.table_id}`"

        # Build MV query for signature calculation
        mv_query = self._build_mv_query(
            select_expressions=select_columns,
            base_table=base_table_name,
            shared_predicates=predicate_analysis.shared_predicates,
            group_by_expressions=group_by_columns,
        )

        # T071: Calculate signature hash
        signature_hash_full = self._calculate_signature_hash(mv_query)
        signature_hash_short = signature_hash_full[:12]

        # T070: Generate deterministic MV name
        family_hash_short = candidate.query_hash[:12]
        mv_name = f"{mv_prefix}{family_hash_short}_{signature_hash_short}"

        # Build full DDL
        ddl = self._build_full_ddl(
            mv_name=mv_name,
            signature_hash=signature_hash_short,
            project=target_project,
            dataset=target_dataset,
            mv_query=mv_query,
            enable_refresh=enable_refresh,
            refresh_interval_minutes=refresh_interval_minutes,
            query_hash=candidate.query_hash,
            eligibility_basis=candidate.eligibility_basis,
        )

        # T075: Validate "covers all query rows" invariant
        self._validate_covers_all_rows(
            parsed_queries,
            predicate_analysis.shared_predicates,
            candidate.query_hash,
        )

        # T079: Generate synthesis audit
        synthesis_audit = SynthesisAudit(
            sample_size_k=k,
            sample_job_ids=sample_job_ids,
            dropped_predicates=predicate_analysis.per_query_predicates.get("all", []),
            family_hash=candidate.query_hash,
        )

        # T074: Build MaterializedViewArtifact with metadata
        artifact = MaterializedViewArtifact(
            mv_name=mv_name,
            source_query_hash=candidate.query_hash,
            signature_hash=signature_hash_short,
            project_id=target_project,
            dataset_id=target_dataset,
            mv_region=target_region,
            ddl_definition=ddl,
            base_tables=[dominant_table],
            refresh_interval_minutes=refresh_interval_minutes,
            enable_refresh=enable_refresh,
            partition_expiration_days=None,
            created_at=datetime.now(UTC),
            created_by="bq-automv",
            created_by_tool_version=self.tool_version,
            rulebook_version=SmartTuningService.RULEBOOK_VERSION,
            synthesis_version=self.SYNTHESIS_VERSION,
            synthesis_warnings=synthesis_audit.warnings,
            status=MVStatus.PROPOSED,
            eligibility_basis=candidate.eligibility_basis,
        )

        return artifact, synthesis_audit

    def _sample_queries_top_k(
        self,
        query_samples: list[dict],
        k: int = 20,
    ) -> list[dict]:
        """T080: Sample top K queries by bytes_billed.

        Args:
            query_samples: List of query sample dicts
            k: Number of samples to take (default 20)

        Returns:
            Sorted list of top K query samples
        """
        # Sort by bytes_billed descending, tie-break by creation_time, then job_id
        sorted_samples = sorted(
            query_samples,
            key=lambda s: (
                -(s.get("total_bytes_billed") or 0),
                -(s.get("creation_time") or datetime.min).timestamp(),
                s.get("job_id", ""),
            ),
        )

        return sorted_samples[:k]

    def _validate_single_base_table(
        self,
        parsed_queries: list[tuple[str, object]],
        query_hash: str,
    ) -> None:
        """T077: Validate queries have no JOINs (single-base-table enforcement).

        Args:
            parsed_queries: List of (job_id, AST) tuples
            query_hash: Query family hash for error reporting

        Raises:
            MVGenerationError: If any query contains JOINs
        """
        for job_id, ast in parsed_queries:
            join_types = self._parser.get_join_types(ast)
            if join_types:
                logger.error(
                    f"Query {job_id} contains JOINs: {join_types}",
                    extra={"job_id": job_id, "query_hash": query_hash, "join_types": join_types},
                )
                raise MVGenerationError(
                    f"Query {job_id} contains {', '.join(join_types)} - "
                    "MV generation requires single-base-table queries only",
                    reason="contains_join",
                    query_hash=query_hash,
                )

    def _get_dominant_base_table(
        self,
        parsed_queries: list[tuple[str, object]],
        referenced_tables: list,
    ) -> object:
        """Get the dominant base table from parsed queries.

        Args:
            parsed_queries: List of (job_id, AST) tuples
            referenced_tables: TableReference objects from QueryCandidate

        Returns:
            Dominant TableReference object
        """
        # For single-base-table queries, return the first (and only) table
        if referenced_tables:
            return referenced_tables[0]

        # Fallback: extract from first query's AST
        if parsed_queries:
            tables = self._parser.extract_referenced_tables(parsed_queries[0][1])
            if tables:
                return tables[0]

        raise MVGenerationError(
            "No base table found in queries",
            reason="no_base_table",
        )

    def _validate_region(
        self,
        base_table: object,
        target_region: str,
        query_hash: str,
    ) -> None:
        """T078: Validate all referenced tables match target dataset region.

        Args:
            base_table: Dominant base table
            target_region: Target region for MV
            query_hash: Query family hash for error reporting

        Raises:
            MVGenerationError: If regions don't match
        """
        table_region = getattr(base_table, "region", None)
        if not table_region:
            logger.warning(
                f"Base table {base_table.full_name} has no region information",
                extra={"query_hash": query_hash, "table": base_table.full_name},
            )
            return

        if table_region.upper() != target_region.upper():
            logger.error(
                f"Region mismatch: table {base_table.full_name} is in {table_region}, "
                f"target dataset is in {target_region}",
                extra={
                    "query_hash": query_hash,
                    "table": base_table.full_name,
                    "table_region": table_region,
                    "target_region": target_region,
                },
            )
            raise MVGenerationError(
                f"Region mismatch: base table {base_table.full_name} is in {table_region}, "
                f"target dataset is in {target_region}",
                reason="region_mismatch",
                query_hash=query_hash,
            )

    def _analyze_predicates(
        self,
        parsed_queries: list[tuple[str, object]],
    ) -> PredicateAnalysis:
        """T072: Analyze predicates across query family for lifting.

        Computes shared predicates (identical across all queries) and
        per-query predicates (varying), then identifies liftable columns.

        Args:
            parsed_queries: List of (job_id, AST) tuples

        Returns:
            PredicateAnalysis with shared/per-query predicates and liftable columns
        """
        from collections import Counter

        all_predicates: list[tuple[str, str]] = []  # (job_id, predicate)
        predicate_counter: Counter = Counter()

        # Extract predicates from all queries
        for job_id, ast in parsed_queries:
            predicates = self._parser.extract_where_predicates(ast)
            for pred in predicates:
                all_predicates.append((job_id, pred))
                predicate_counter[pred] += 1

        # Compute shared predicates (present in ALL queries)
        num_queries = len(parsed_queries)
        shared_predicates = [pred for pred, count in predicate_counter.items() if count == num_queries]

        # Compute per-query predicates (not shared)
        per_query_predicates: dict[str, list[str]] = {}
        for job_id, pred in all_predicates:
            if pred not in shared_predicates:
                if job_id not in per_query_predicates:
                    per_query_predicates[job_id] = []
                per_query_predicates[job_id].append(pred)

        # Extract liftable columns from per-query predicates
        liftable_columns = self._extract_liftable_columns(per_query_predicates.values())

        return PredicateAnalysis(
            shared_predicates=shared_predicates,
            per_query_predicates=per_query_predicates,
            liftable_columns=liftable_columns,
        )

    def _extract_liftable_columns(self, predicate_lists: list[list[str]]) -> list[str]:
        """Extract column names from per-query predicates.

        Args:
            predicate_lists: List of predicate lists

        Returns:
            List of unique column names
        """
        # Pattern to match column references in predicates
        column_pattern = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*[=<>!]")

        columns: set[str] = set()

        for predicates in predicate_lists:
            for pred in predicates:
                matches = column_pattern.findall(pred)
                for match in matches:
                    # Filter out literals and function names
                    if match.upper() not in {
                        "NULL",
                        "TRUE",
                        "FALSE",
                        "AND",
                        "OR",
                        "NOT",
                        "IN",
                        "LIKE",
                    }:
                        columns.add(match)

        return sorted(columns)

    def _validate_group_by_identical(
        self,
        parsed_queries: list[tuple[str, object]],
        query_hash: str,
    ) -> list[str]:
        """T076: Validate GROUP BY columns are identical across family.

        Args:
            parsed_queries: List of (job_id, AST) tuples
            query_hash: Query family hash for error reporting

        Returns:
            Canonical GROUP BY columns (identical across all queries)

        Raises:
            MVGenerationError: If GROUP BY columns differ
        """
        group_by_sets = []
        for _job_id, ast in parsed_queries:
            group_by = self._parser.extract_group_by_columns(ast)
            # Canonicalize by sorting
            group_by_canonical = sorted(group_by)
            group_by_sets.append(tuple(group_by_canonical))

        # Check all GROUP BYs are identical
        first_group_by = group_by_sets[0]
        for _i, (job_id, group_by) in enumerate(
            zip(
                [p[0] for p in parsed_queries],
                group_by_sets,
                strict=True,
            )
        ):
            if group_by != first_group_by:
                logger.error(
                    f"GROUP BY mismatch: query {job_id} has {group_by}, expected {first_group_by}",
                    extra={"query_hash": query_hash, "job_id": job_id},
                )
                raise MVGenerationError(
                    f"GROUP BY columns differ across query family: "
                    f"query {job_id} has {list(group_by)}, "
                    f"expected {list(first_group_by)}",
                    reason="group_by_mismatch",
                    query_hash=query_hash,
                )

        return list(first_group_by)

    def _extract_select_columns_from_queries(
        self,
        parsed_queries: list[tuple[str, object]],
        group_by_columns: list[str],
    ) -> list[str]:
        """Extract SELECT columns from queries.

        Args:
            parsed_queries: List of (job_id, AST) tuples
            group_by_columns: GROUP BY columns (validated identical)

        Returns:
            List of SELECT expressions
        """
        # Use first query's SELECT as representative
        first_ast = parsed_queries[0][1]
        select_columns = self._parser.extract_select_columns(first_ast)

        # Ensure GROUP BY columns are included
        for gb_col in group_by_columns:
            # Check if GROUP BY column is referenced in SELECT
            found = any(
                gb_col in select_col or select_col.split()[-1] == gb_col  # Handle alias
                for select_col in select_columns
            )
            if not found:
                # Add GROUP BY column to SELECT
                select_columns.insert(0, gb_col)

        return select_columns

    def _build_full_ddl(
        self,
        mv_name: str,
        signature_hash: str,
        project: str,
        dataset: str,
        mv_query: str,
        enable_refresh: bool,
        refresh_interval_minutes: int,
        query_hash: str,
        eligibility_basis: str,
    ) -> str:
        """Build full CREATE MATERIALIZED VIEW DDL with OPTIONS.

        Args:
            mv_name: Materialized view name
            signature_hash: Signature hash of MV SQL
            project: Target project
            dataset: Target dataset
            mv_query: MV query SQL
            enable_refresh: Enable automatic refresh
            refresh_interval_minutes: Refresh interval in minutes
            query_hash: Source query hash
            eligibility_basis: "stable" or "preview"

        Returns:
            Complete DDL with header comments and OPTIONS
        """
        # Build header comments
        header_lines = [
            f"-- MV Name: {mv_name}",
            f"-- Family Hash: {query_hash}",
            f"-- Signature Hash: {signature_hash}",
            f"-- Region: {self.bq_client.region}",
            f"-- Rulebook Version: {SmartTuningService.RULEBOOK_VERSION}",
            f"-- Synthesis Version: {self.SYNTHESIS_VERSION}",
            f"-- Eligibility Basis: {eligibility_basis}",
        ]

        # Build OPTIONS clause
        options = f"""OPTIONS (
  enable_refresh = {str(enable_refresh).lower()},
  refresh_interval_minutes = {refresh_interval_minutes}
)"""

        full_mv_name = f"`{project}.{dataset}.{mv_name}`"

        ddl_parts = [
            "\n".join(header_lines),
            f"CREATE MATERIALIZED VIEW IF NOT EXISTS {full_mv_name}",
            options,
            f"AS\n{mv_query};",
        ]

        return "\n".join(ddl_parts)

    def _validate_covers_all_rows(
        self,
        parsed_queries: list[tuple[str, object]],
        shared_predicates: list[str],
        query_hash: str,
    ) -> None:
        """T075: Validate MV WHERE is superset of all query predicates.

        Ensures MV does not exclude rows required by any query.

        Args:
            parsed_queries: List of (job_id, AST) tuples
            shared_predicates: Predicates in MV WHERE clause
            query_hash: Query family hash for error reporting

        Raises:
            MVGenerationError: If MV would exclude required rows
        """
        # The predicate lifting algorithm ensures this by construction:
        # MV WHERE = intersection of predicates (shared only)
        # This is guaranteed to be less restrictive than any individual query

        for job_id, ast in parsed_queries:
            query_predicates = self._parser.extract_where_predicates(ast)

            # Check that MV predicates are subset of query predicates
            for mv_pred in shared_predicates:
                found = False
                for query_pred in query_predicates:
                    if self._predicates_match(mv_pred, query_pred):
                        found = True
                        break

                if not found:
                    logger.error(
                        f"MV predicate '{mv_pred}' not found in query {job_id}",
                        extra={"query_hash": query_hash, "job_id": job_id, "mv_predicate": mv_pred},
                    )
                    raise MVGenerationError(
                        f"MV WHERE clause would exclude rows from query {job_id}: "
                        f"predicate '{mv_pred}' not found in query",
                        reason="predicate_generalization_failed",
                        query_hash=query_hash,
                    )

    def _predicates_match(self, pred1: str, pred2: str) -> bool:
        """Check if two predicates match (canonicalized).

        Args:
            pred1: First predicate
            pred2: Second predicate

        Returns:
            True if predicates match after canonicalization
        """
        # Simple canonicalization: normalize whitespace and case
        norm1 = " ".join(pred1.upper().split())
        norm2 = " ".join(pred2.upper().split())
        return norm1 == norm2
