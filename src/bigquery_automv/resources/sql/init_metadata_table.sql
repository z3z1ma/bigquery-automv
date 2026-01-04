CREATE TABLE IF NOT EXISTS {full_table_name} (
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
PARTITION BY TIMESTAMP_TRUNC(created_at, DAY)
CLUSTER BY status
OPTIONS (
  partition_expiration_days = 365,
  description = "BigQuery AutoMV metadata table for tracking materialized views"
)
