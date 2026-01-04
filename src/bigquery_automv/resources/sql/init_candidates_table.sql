CREATE TABLE IF NOT EXISTS {full_table_name} (
  query_hash STRING NOT NULL,
  representative_query STRING,
  execution_count INT64,
  bytes_billed_total INT64,
  total_bytes_processed INT64,
  slot_ms_total INT64,
  impact_score FLOAT64,
  dollar_cost_est_on_demand FLOAT64,
  impact_model_version STRING,
  rulebook_version STRING,
  statement_type STRING,
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  referenced_tables ARRAY<STRUCT<
    project_id STRING,
    dataset_id STRING,
    table_id STRING,
    region STRING,
    full_name STRING
  >>,
  smart_tuning_eligible BOOL,
  eligibility_basis STRING,
  smart_tuning_reasons ARRAY<STRING>,
  analysis_start_date TIMESTAMP,
  analysis_end_date TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY smart_tuning_eligible, impact_score
OPTIONS (
  description = "BigQuery AutoMV query candidates from analysis"
)
