"""Async BigQuery client wrapper with region validation and error handling.

This module provides an async-friendly wrapper around google-cloud-bigquery with
specialized methods for INFORMATION_SCHEMA.JOBS queries, region validation, and
materialized view operations.
"""

from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Self

from google.api_core import retry
from google.api_core.exceptions import (
    AlreadyExists,
    BadRequest,
    Forbidden,
    NotFound,
    ServerError,
)
from google.cloud.bigquery import (
    Client as BQClient,
)
from google.cloud.bigquery import (
    QueryJobConfig,
)
from google.cloud.bigquery.job import QueryJob


class BigQueryError(Exception):
    """Base exception for BigQuery errors."""

    def __init__(self, message: str, *, original: Exception | None = None) -> None:
        self.message = message
        self.original = original
        super().__init__(message)


class PermissionError(BigQueryError):
    """Raised when the client lacks permissions for an operation."""


class NotFoundError(BigQueryError):
    """Raised when a requested resource doesn't exist."""


class QuotaError(BigQueryError):
    """Raised when a quota limit is exceeded."""


class RegionMismatchError(BigQueryError):
    """Raised when regions don't match (e.g., dataset vs table)."""


class BigQueryClientError(BigQueryError):
    """Raised for general BigQuery client errors."""


@dataclass(frozen=True)
class TableIdentifier:
    """Identifies a BigQuery table."""

    project_id: str
    dataset_id: str
    table_id: str

    @property
    def full_name(self) -> str:
        """Return the fully qualified table name."""
        return f"{self.project_id}.{self.dataset_id}.{self.table_id}"

    @classmethod
    def from_string(cls, table_name: str) -> Self:
        """Parse a table name string into a TableIdentifier.

        Args:
            table_name: Table name in formats:
                - "project.dataset.table"
                - "dataset.table" (uses default project)
                - "table" (uses default project and dataset)

        Raises:
            ValueError: If table_name format is invalid
        """
        parts = table_name.split(".")
        if len(parts) == 3:
            return cls(project_id=parts[0], dataset_id=parts[1], table_id=parts[2])
        if len(parts) == 2:
            return cls(project_id="", dataset_id=parts[0], table_id=parts[1])
        if len(parts) == 1:
            return cls(project_id="", dataset_id="", table_id=parts[0])
        msg = f"Invalid table name format: {table_name}"
        raise ValueError(msg)


@dataclass(frozen=True)
class QueryResult:
    """Result of a BigQuery query execution."""

    rows: list[dict]
    total_rows: int | None = None
    bytes_processed: int | None = None
    job_id: str | None = None
    cache_hit: bool | None = None


class BigQueryClient:
    """Async wrapper for google-cloud-bigquery with region validation and error handling.

    This client provides async-compatible methods for querying BigQuery, validating
    regions, and managing materialized views. While the underlying google-cloud-bigquery
    library is synchronous, this wrapper uses async generators and context managers
    to integrate cleanly with async applications.

    Example:
        ```python
        async with BigQueryClient(project_id="my-project", region="US") as client:
            result = await client.run_query("SELECT 1")
            print(result.rows)
        ```
    """

    _DEFAULT_LOCATION = "US"
    _REGION_PROJECT_PREFIX = "region-"

    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        credentials: object | None = None,
    ) -> None:
        """Initialize the BigQuery client.

        Args:
            project_id: GCP project ID
            region: BigQuery region (e.g., "US", "eu", "region-us")
            credentials: Optional google.auth.credentials.Credentials object.
                If None, uses Application Default Credentials (ADC).
        """
        self._project_id = project_id
        self._region = self._normalize_region(region)
        self._client = BQClient(
            project=project_id,
            location=self._region,
            credentials=credentials,
        )

    @property
    def project_id(self) -> str:
        """Return the project ID."""
        return self._project_id

    @property
    def region(self) -> str:
        """Return the normalized region name."""
        return self._region

    def _normalize_region(self, region: str) -> str:
        """Normalize region name to BigQuery location format.

        For the location parameter in queries, US/EU should not have a "region-" prefix.
        Other regions like "asia-northeast1" are used as-is.

        Args:
            region: Region name like "US", "eu", "region-us", "region-eu", "asia-northeast1"

        Returns:
            Normalized region name for use as location parameter (e.g., "US", "EU", "ASIA-NORTHEAST1")
        """
        region_upper = region.upper().strip()

        # Handle explicit "region-" prefix by stripping it for location
        if region_upper.startswith(self._REGION_PROJECT_PREFIX):
            # "region-us" -> "US", "region-eu" -> "EU"
            suffix = region_upper[len(self._REGION_PROJECT_PREFIX) :]
            if suffix in {"US", "EU"}:
                return suffix
            # Keep other regions as-is (e.g., "region-asia-northeast1" -> "ASIA-NORTHEAST1")
            return suffix

        # US and EU multi-regions are used directly
        return region_upper

    def _get_region_for_information_schema(self) -> str:
        """Return the region name for INFORMATION_SCHEMA queries.

        INFORMATION_SCHEMA.JOBS queries use region-scoped projects like
        `region-us.INFORMATION_SCHEMA.JOBS`.

        Returns:
            Region project name like "region-us" or "region-eu"
        """
        region_upper = self._region.upper()
        # For US/EU multi-regions, add "region-" prefix for INFORMATION_SCHEMA
        if region_upper in {"US", "EU"}:
            return f"{self._REGION_PROJECT_PREFIX}{region_upper.lower()}"
        # For other regions, add "region-" prefix if not already present
        if not region_upper.startswith(self._REGION_PROJECT_PREFIX):
            return f"{self._REGION_PROJECT_PREFIX}{region_upper.lower()}"
        return region_upper.lower()

    @asynccontextmanager
    async def _execute_with_retry(self, operation_name: str) -> AsyncGenerator[None]:
        """Context manager for executing operations with retry logic.

        Args:
            operation_name: Description of the operation for error messages

        Raises:
            PermissionError: On permission errors
            NotFoundError: On resource not found errors
            QuotaError: On quota exceeded errors
            BigQueryClientError: On other errors
        """
        try:
            yield
        except Forbidden as e:
            msg = f"Permission denied for {operation_name}: {e.message}"
            raise PermissionError(msg, original=e) from e
        except NotFound as e:
            msg = f"Resource not found during {operation_name}: {e.message}"
            raise NotFoundError(msg, original=e) from e
        except AlreadyExists:
            # This is not an error for idempotent operations
            # Let caller handle this
            raise
        except BadRequest as e:
            if "quota" in str(e).lower():
                msg = f"Quota exceeded during {operation_name}: {e.message}"
                raise QuotaError(msg, original=e) from e
            msg = f"Bad request during {operation_name}: {e.message}"
            raise BigQueryClientError(msg, original=e) from e
        except ServerError as e:
            msg = f"Server error during {operation_name}: {e.message}"
            raise BigQueryClientError(msg, original=e) from e
        except Exception as e:
            msg = f"Unexpected error during {operation_name}: {e!s}"
            raise BigQueryClientError(msg, original=e) from e

    async def run_query(
        self,
        sql: str,
        *,
        job_config: QueryJobConfig | None = None,
        query_params: Iterable[tuple[str, str, object]] | None = None,
    ) -> QueryResult:
        """Execute a SQL query and return results.

        Args:
            sql: SQL query string
            job_config: Optional QueryJobConfig for query parameters
            query_params: Optional list of (name, type, value) tuples for parameterized queries

        Returns:
            QueryResult with rows and metadata

        Raises:
            PermissionError: If lacking permissions
            NotFoundError: If referenced resources don't exist
            QuotaError: If quota is exceeded
            BigQueryClientError: For other errors
        """
        async with self._execute_with_retry("query execution"):
            if query_params:
                # Build QueryJobConfig if parameters provided
                from google.cloud.bigquery import ScalarQueryParameter

                if job_config is None:
                    job_config = QueryJobConfig()
                job_config.query_parameters = [
                    ScalarQueryParameter(name, param_type, value) for name, param_type, value in query_params
                ]

            # Use retry for transient errors
            retry_strategy = retry.Retry(
                predicate=retry.if_exception_type(
                    ServerError,
                ),
                initial=1.0,
                maximum=60.0,
                multiplier=2.0,
            )

            job: QueryJob = self._client.query(
                sql,
                job_config=job_config,
                location=self._region,
                retry=retry_strategy,
            )

            # Wait for completion
            result = job.result()

            rows = [dict(row) for row in result]

            return QueryResult(
                rows=rows,
                total_rows=result.total_rows,
                bytes_processed=result.total_bytes_processed,
                job_id=job.job_id,
                cache_hit=job.cache_hit,
            )

    async def query_in_batches(
        self,
        sql: str,
        *,
        batch_size: int = 10000,
        max_results: int | None = None,
    ) -> AsyncGenerator[list[dict]]:
        """Execute a query and yield results in batches.

        This is useful for large result sets that shouldn't be loaded entirely
        into memory.

        Args:
            sql: SQL query string
            batch_size: Number of rows per batch
            max_results: Maximum total rows to return (None for unlimited)

        Yields:
            List of row dictionaries

        Raises:
            PermissionError: If lacking permissions
            NotFoundError: If referenced resources don't exist
            QuotaError: If quota is exceeded
            BigQueryClientError: For other errors
        """
        async with self._execute_with_retry("batch query execution"):
            retry_strategy = retry.Retry(
                predicate=retry.if_exception_type(ServerError),
                initial=1.0,
                maximum=60.0,
                multiplier=2.0,
            )

            job: QueryJob = self._client.query(
                sql,
                location=self._region,
                retry=retry_strategy,
            )

            # Use iterator to fetch rows
            row_iter = job.result(max_results=max_results)

            batch: list[dict] = []
            for row in row_iter:
                batch.append(dict(row))
                if len(batch) >= batch_size:
                    yield batch
                    batch = []

            # Yield remaining rows
            if batch:
                yield batch

    async def dataset_exists(self, dataset_id: str, *, project_id: str | None = None) -> bool:
        """Check if a dataset exists.

        Args:
            dataset_id: Dataset ID
            project_id: Project ID (defaults to client's project)

        Returns:
            True if dataset exists, False otherwise
        """
        project = project_id or self._project_id
        try:
            async with self._execute_with_retry("dataset existence check"):
                self._client.get_dataset(f"{project}.{dataset_id}")
                return True
        except NotFound:
            return False
        except PermissionError:
            # If we can't access it, treat as not existing from our perspective
            return False

    async def table_exists(self, table: TableIdentifier) -> bool:
        """Check if a table exists.

        Args:
            table: TableIdentifier for the table

        Returns:
            True if table exists, False otherwise
        """
        try:
            async with self._execute_with_retry("table existence check"):
                self._client.get_table(table.full_name)
                return True
        except NotFound:
            return False
        except PermissionError:
            return False

    async def get_dataset_region(self, dataset_id: str, *, project_id: str | None = None) -> str:
        """Get the location/region of a dataset.

        Args:
            dataset_id: Dataset ID
            project_id: Project ID (defaults to client's project)

        Returns:
            Dataset location string (e.g., "US", "eu", "asia-northeast1")

        Raises:
            NotFoundError: If dataset doesn't exist
            PermissionError: If lacking permissions to access dataset
            BigQueryClientError: For other errors
        """
        project = project_id or self._project_id
        async with self._execute_with_retry("get dataset region"):
            dataset = self._client.get_dataset(f"{project}.{dataset_id}")
            return dataset.location

    async def get_table_region(self, table: TableIdentifier) -> str:
        """Get the region of a table by querying its dataset.

        Args:
            table: TableIdentifier for the table

        Returns:
            Table region (e.g., "US", "eu")

        Raises:
            NotFoundError: If table or dataset doesn't exist
            PermissionError: If lacking permissions
            BigQueryClientError: For other errors
        """
        return await self.get_dataset_region(
            dataset_id=table.dataset_id,
            project_id=table.project_id or self._project_id,
        )

    async def validate_region_match(
        self,
        tables: list[TableIdentifier],
        target_region: str,
    ) -> None:
        """Validate that all tables are in the target region.

        Args:
            tables: List of TableIdentifier objects to validate
            target_region: Required region (e.g., "US", "eu")

        Raises:
            RegionMismatchError: If any table is in a different region
            NotFoundError: If any table or dataset doesn't exist
            PermissionError: If lacking permissions
            BigQueryClientError: For other errors
        """
        mismatched: list[tuple[str, str]] = []

        for table in tables:
            table_region = await self.get_table_region(table)
            if table_region.upper() != target_region.upper():
                mismatched.append((table.full_name, table_region))

        if mismatched:
            table_list = "\n".join(f"  - {table_name}: {region}" for table_name, region in mismatched)
            msg = (
                f"Region mismatch: Tables must be in {target_region.upper()} region.\nMismatched tables:\n{table_list}"
            )
            raise RegionMismatchError(msg)

    async def execute_ddl(
        self,
        ddl: str,
        *,
        job_id: str | None = None,
    ) -> QueryResult:
        """Execute a DDL statement (CREATE, DROP, ALTER, etc.).

        Args:
            ddl: DDL SQL statement
            job_id: Optional job ID for the query job

        Returns:
            QueryResult with execution metadata

        Raises:
            PermissionError: If lacking permissions
            NotFoundError: If referenced resources don't exist
            QuotaError: If quota is exceeded
            BigQueryClientError: For other errors
        """
        job_config = QueryJobConfig()
        if job_id:
            job_config.job_id = job_id

        return await self.run_query(ddl, job_config=job_config)

    async def create_materialized_view(
        self,
        mv_name: str,
        dataset_id: str,
        query: str,
        *,
        project_id: str | None = None,
        enable_refresh: bool = True,
        refresh_interval_minutes: int = 0,
        description: str | None = None,
        if_not_exists: bool = False,
    ) -> QueryResult:
        """Create a materialized view.

        Args:
            mv_name: Materialized view name
            dataset_id: Target dataset ID
            query: SQL query for the view definition (AS SELECT ...)
            project_id: Project ID (defaults to client's project)
            enable_refresh: Enable automatic refresh
            refresh_interval_minutes: Refresh interval in minutes (0 = manual)
            description: Optional description
            if_not_exists: Use CREATE MATERIALIZED VIEW IF NOT EXISTS

        Returns:
            QueryResult with execution metadata

        Raises:
            PermissionError: If lacking permissions
            NotFoundError: If dataset doesn't exist
            QuotaError: If quota is exceeded
            BigQueryClientError: For other errors
        """
        project = project_id or self._project_id
        full_mv_name = f"{project}.{dataset_id}.{mv_name}"

        options = [
            f"enable_refresh = {str(enable_refresh).lower()}",
            f"refresh_interval_minutes = {refresh_interval_minutes}",
        ]

        ddl_parts = [
            "CREATE MATERIALIZED VIEW",
            "IF NOT EXISTS" if if_not_exists else "",
            full_mv_name,
            f"OPTIONS ({', '.join(options)})",
            f"AS\n{query}",
        ]

        ddl = " ".join(ddl_parts)

        if description:
            # Add description as a comment
            ddl = f"-- {description}\n" + ddl

        return await self.execute_ddl(ddl)

    async def drop_materialized_view(
        self,
        mv_name: str,
        dataset_id: str,
        *,
        project_id: str | None = None,
        if_exists: bool = False,
    ) -> QueryResult:
        """Drop a materialized view.

        Args:
            mv_name: Materialized view name
            dataset_id: Dataset ID
            project_id: Project ID (defaults to client's project)
            if_exists: Use DROP MATERIALIZED VIEW IF EXISTS

        Returns:
            QueryResult with execution metadata

        Raises:
            PermissionError: If lacking permissions
            NotFoundError: If view doesn't exist (and if_exists=False)
            BigQueryClientError: For other errors
        """
        project = project_id or self._project_id
        full_mv_name = f"{project}.{dataset_id}.{mv_name}"

        exists_clause = "IF EXISTS" if if_exists else ""
        ddl = f"DROP MATERIALIZED VIEW {exists_clause} {full_mv_name}"

        return await self.execute_ddl(ddl)

    async def get_materialized_view(
        self,
        mv_name: str,
        dataset_id: str,
        *,
        project_id: str | None = None,
    ) -> dict:
        """Get materialized view metadata.

        Args:
            mv_name: Materialized view name
            dataset_id: Dataset ID
            project_id: Project ID (defaults to client's project)

        Returns:
            Dictionary with MV metadata including:
                - view_name: str
                - query: str (SQL definition)
                - last_refresh_time: datetime | None
                - refresh_interval_minutes: int
                - enable_refresh: bool

        Raises:
            NotFoundError: If MV doesn't exist
            PermissionError: If lacking permissions
            BigQueryClientError: For other errors
        """
        project = project_id or self._project_id
        full_mv_name = f"{project}.{dataset_id}.{mv_name}"

        async with self._execute_with_retry("get materialized view"):
            table = self._client.get_table(full_mv_name)

            return {
                "view_name": table.full_name,
                "query": table.view_query,
                "last_refresh_time": table.modified,
                "refresh_interval_minutes": table.refresh_interval_minutes
                if hasattr(table, "refresh_interval_minutes")
                else None,
                "enable_refresh": table.enable_refresh if hasattr(table, "enable_refresh") else None,
                "num_bytes": table.num_bytes,
                "num_rows": table.num_rows,
                "creation_time": table.created,
            }

    async def initialize_metadata_table(
        self,
        dataset_id: str,
        table_name: str = "automv_metadata",
        *,
        project_id: str | None = None,
    ) -> QueryResult:
        """Create the metadata table for tracking MV artifacts.

        Args:
            dataset_id: Dataset ID for the metadata table
            table_name: Table name (default: "automv_metadata")
            project_id: Project ID (defaults to client's project)

        Returns:
            QueryResult with execution metadata

        Raises:
            PermissionError: If lacking permissions
            NotFoundError: If dataset doesn't exist
            BigQueryClientError: For other errors
        """
        project = project_id or self._project_id
        full_table_name = f"{project}.{dataset_id}.{table_name}"

        ddl = f"""CREATE TABLE IF NOT EXISTS {full_table_name} (
  mv_name STRING,
  source_query_hash STRING,
  signature_hash STRING,
  created_at TIMESTAMP,
  base_tables ARRAY<STRUCT<
    project_id STRING,
    dataset_id STRING,
    table_id STRING
  >>,
  project_id STRING,
  dataset_id STRING,
  mv_region STRING,
  status STRING,
  eligibility_basis STRING,
  refresh_interval_minutes INT64,
  ddl_definition STRING,
  created_by_tool_version STRING,
  rulebook_version STRING,
  synthesis_version STRING,
  synthesis_warnings ARRAY<STRING>,
  last_refreshed TIMESTAMP,
  usage_count INT64,
  total_bytes_saved INT64,
  total_slot_ms_saved INT64,
  last_used TIMESTAMP
)
CLUSTER BY status
OPTIONS (
  partition_expiration_days = 365,
  description = "BigQuery AutoMV metadata table for tracking materialized views"
)"""

        return await self.execute_ddl(ddl)

    async def insert_metadata(
        self,
        dataset_id: str,
        metadata: dict,
        *,
        table_name: str = "automv_metadata",
        project_id: str | None = None,
    ) -> QueryResult:
        """Insert a record into the metadata table.

        Args:
            dataset_id: Dataset ID for the metadata table
            metadata: Dictionary with metadata fields
            table_name: Table name (default: "automv_metadata")
            project_id: Project ID (defaults to client's project)

        Returns:
            QueryResult with execution metadata

        Raises:
            PermissionError: If lacking permissions
            NotFoundError: If metadata table doesn't exist
            BigQueryClientError: For other errors
        """
        project = project_id or self._project_id
        full_table_name = f"{project}.{dataset_id}.{table_name}"

        # Build INSERT statement with field names and values
        field_names = ", ".join(metadata.keys())
        # Convert dict values to SQL literal format
        value_placeholders = ", ".join(f"@{k}" for k in metadata.keys())

        sql = f"INSERT INTO {full_table_name} ({field_names}) VALUES ({value_placeholders})"

        query_params = [(k, "STRING", v) for k, v in metadata.items()]

        return await self.run_query(sql, query_params=query_params)

    async def query_information_schema_jobs(
        self,
        start_date: datetime,
        end_date: datetime,
        *,
        additional_filters: list[str] | None = None,
        project_filter: str | None = None,
        min_bytes_billed: int | None = None,
        statement_types: list[str] | None = None,
    ) -> QueryResult:
        """Query INFORMATION_SCHEMA.JOBS with region-scoped access.

        This method queries region-scoped INFORMATION_SCHEMA.JOBS to analyze
        query history for optimization candidates.

        Args:
            start_date: Start of time range (inclusive)
            end_date: End of time range (inclusive)
            additional_filters: Additional WHERE clause filters
            project_filter: Filter to specific project_id
            min_bytes_billed: Minimum total_bytes_billed threshold
            statement_types: Filter to statement types (e.g., ['SELECT'])

        Returns:
            QueryResult with job history rows

        Raises:
            PermissionError: If lacking permissions
            BigQueryClientError: For other errors
        """
        region_project = self._get_region_for_information_schema()

        # Build WHERE clauses
        where_clauses = [
            f"creation_time >= TIMESTAMP('{start_date.isoformat()}')",
            f"creation_time <= TIMESTAMP('{end_date.isoformat()}')",
            "job_type = 'QUERY'",
            "state = 'DONE'",
        ]

        if project_filter:
            where_clauses.append(f"project_id = '{project_filter}'")

        if statement_types:
            types_str = ", ".join(f"'{st}'" for st in statement_types)
            where_clauses.append(f"statement_type IN ({types_str})")

        if min_bytes_billed is not None:
            where_clauses.append(f"total_bytes_billed >= {min_bytes_billed}")

        if additional_filters:
            where_clauses.extend(additional_filters)

        where_clause = " AND ".join(where_clauses)

        # Select relevant columns for analysis
        sql = f"""SELECT
  project_id,
  job_id,
  user_email,
  creation_time,
  start_time,
  end_time,
  query,
  query_info.query_hashes.normalized_literals AS normalized_literals,
  referenced_tables,
  total_bytes_processed,
  total_bytes_billed,
  total_slot_ms,
  statement_type,
  job_type,
  state,
  cache_hit,
  destination_table,
  reservation_id
FROM `{region_project}.INFORMATION_SCHEMA.JOBS`
WHERE {where_clause}
ORDER BY creation_time DESC"""

        return await self.run_query(sql)

    async def query_materialized_view_statistics(
        self,
        start_date: datetime,
        end_date: datetime,
        mv_name: str | None = None,
        *,
        chosen_only: bool = True,
    ) -> QueryResult:
        """Query materialized view usage statistics.

        This method queries INFORMATION_SCHEMA.JOBS to find queries that used
        materialized views via Smart Tuning.

        Args:
            start_date: Start of time range
            end_date: End of time range
            mv_name: Optional MV name filter
            chosen_only: Only return jobs where MV was chosen (default: True)

        Returns:
            QueryResult with MV statistics rows

        Raises:
            PermissionError: If lacking permissions
            BigQueryClientError: For other errors
        """
        region_project = self._get_region_for_information_schema()

        where_clauses = [
            f"creation_time >= TIMESTAMP('{start_date.isoformat()}')",
            f"creation_time <= TIMESTAMP('{end_date.isoformat()}')",
            "job_type = 'QUERY'",
            "state = 'DONE'",
        ]

        if mv_name:
            where_clauses.append(f"mv.table_reference.table_id = '{mv_name}'")

        if chosen_only:
            where_clauses.append("mv.chosen = TRUE")

        where_clause = " AND ".join(where_clauses)

        sql = f"""SELECT
  job_id,
  project_id,
  user_email,
  creation_time,
  total_bytes_processed,
  total_bytes_billed,
  total_slot_ms,
  mv.table_reference.project_id AS mv_project_id,
  mv.table_reference.dataset_id AS mv_dataset_id,
  mv.table_reference.table_id AS mv_name,
  mv.chosen AS mv_chosen,
  mv.rejected_reason AS mv_rejection_reason
FROM `{region_project}.INFORMATION_SCHEMA.JOBS`,
  UNNEST(materialized_view_statistics.materialized_view) mv
WHERE {where_clause}
ORDER BY creation_time DESC"""

        return await self.run_query(sql)

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit."""
        # Close the underlying BigQuery client
        self._client.close()

    def close(self) -> None:
        """Close the BigQuery client connection.

        This method closes the underlying google-cloud-bigquery client
        and releases any associated resources.
        """
        self._client.close()
