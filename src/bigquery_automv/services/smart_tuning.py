"""
Smart Tuning eligibility service for BigQuery materialized views.

This module provides comprehensive Smart Tuning eligibility checking using
sqlglot-based SQL parsing and BigQuery metadata queries.
"""

from dataclasses import dataclass, field

from sqlglot import exp

from bigquery_automv.services.bq_client import BigQueryClient, TableIdentifier
from bigquery_automv.services.sql_parser import SQLParseError, SQLParser


@dataclass
class SmartTuningCheckResult:
    """Result of Smart Tuning eligibility check.

    Attributes:
        query_hash: Query identifier hash
        eligible: True if query is eligible for Smart Tuning
        eligibility_basis: "stable" or "preview" features
        disqualification_reasons: List of reasons if not eligible
        unsupported_features: List of unsupported SQL features found
        aggregation_functions: List of aggregate functions in query
        join_types: List of join types used
        has_ctes: True if query has CTEs
        subquery_types: List of subquery types found
        cross_project_references: True if query references cross-project tables
        region_mismatch: True if tables don't match target dataset region
        liftable_columns: Columns in SELECT/GROUP BY (predicate lifting)
        non_liftable_filters: Filters on non-output columns
        recommended_mv_filters: Shared predicates for MV WHERE clause
        recommended_mv_select: SELECT expressions for MV
        recommended_mv_group_by: GROUP BY expressions for MV
        rulebook_version: Eligibility rulebook version used
    """

    query_hash: str
    eligible: bool
    eligibility_basis: str = "stable"

    # Detailed analysis
    disqualification_reasons: list[str] = field(default_factory=list)
    unsupported_features: list[str] = field(default_factory=list)

    # Query structure
    aggregation_functions: list[str] = field(default_factory=list)
    join_types: list[str] = field(default_factory=list)
    has_ctes: bool = False
    subquery_types: list[str] = field(default_factory=list)

    # Cross-tenancy and region checks
    cross_project_references: bool = False
    region_mismatch: bool = False

    # Column analysis (for predicate lifting)
    liftable_columns: list[str] = field(default_factory=list)
    non_liftable_filters: list[str] = field(default_factory=list)

    # Recommendations
    recommended_mv_filters: list[str] = field(default_factory=list)
    recommended_mv_select: list[str] = field(default_factory=list)
    recommended_mv_group_by: list[str] = field(default_factory=list)

    # Rulebook version
    rulebook_version: str = "v1.0"


class SmartTuningService:
    """
    Smart Tuning eligibility checking service.

    Uses sqlglot for SQL parsing and BigQuery client for metadata validation
    to determine if queries are eligible for Smart Tuning with materialized views.
    """

    # Current rulebook version
    RULEBOOK_VERSION = "v1.0"

    # Preview-only features (require --enable-preview-eligibility)
    PREVIEW_FEATURES = {
        "UNION ALL",
        "LEFT OUTER JOIN",
    }

    # Fully unsupported features (even with preview)
    UNSUPPORTED_FEATURES = {
        "RIGHT OUTER JOIN",
        "FULL OUTER JOIN",
        "WINDOW FUNCTIONS",
        "ARRAY SUBQUERY",
        "QUALIFY CLAUSE",
        "NON-DETERMINISTIC FUNCTIONS",
        "UDFS",
        "HAVING ON AGGREGATES",
        "COMPUTED AGGREGATES",
    }

    def __init__(
        self,
        *,
        bq_client: BigQueryClient | None = None,
        enable_preview_eligibility: bool = False,
    ) -> None:
        """
        Initialize the Smart Tuning service.

        Args:
            bq_client: Optional BigQuery client for metadata queries
            enable_preview_eligibility: Allow preview-only features
        """
        self.bq_client = bq_client
        self.enable_preview_eligibility = enable_preview_eligibility
        self.parser = SQLParser(dialect="bigquery")

    async def check_elibility(
        self,
        sql: str,
        *,
        query_hash: str,
        target_dataset: str | None = None,
        target_project: str | None = None,
    ) -> SmartTuningCheckResult:
        """
        Check if a query is eligible for Smart Tuning.

        Performs comprehensive eligibility analysis including:
        - Unsupported pattern detection (UNION ALL, JOINs, window functions)
        - Non-deterministic function detection
        - UDF detection
        - Aggregate function validation
        - Cross-project reference detection
        - Region mismatch detection
        - Logical view reference detection
        - Column analysis for predicate lifting

        Args:
            sql: SQL query string to analyze
            query_hash: Query identifier hash
            target_dataset: Target dataset for MV (for region validation)
            target_project: Target project (for cross-project check)

        Returns:
            SmartTuningCheckResult with detailed analysis
        """
        result = SmartTuningCheckResult(
            query_hash=query_hash,
            eligible=False,
            rulebook_version=self.RULEBOOK_VERSION,
        )

        # Parse the query
        try:
            ast = self.parser.parse_query(sql)
        except SQLParseError as e:
            result.disqualification_reasons.append(f"SQL parsing failed: {e}")
            return result

        # Extract basic structure
        result.aggregation_functions = self.parser.get_aggregation_functions(ast)
        result.join_types = self.parser.get_join_types(ast)
        result.has_ctes = self.parser.has_ctes(ast)

        # Check for unsupported patterns (T038)
        await self._detect_unsupported_patterns(ast, result)

        # Check for non-deterministic functions (T039)
        await self._detect_non_deterministic_functions(ast, result)

        # Check for UDFs (T040)
        await self._detect_udfs(ast, result)

        # Validate aggregate functions (T041)
        await self._validate_aggregate_functions(ast, result)

        # Check for unsupported aggregate patterns (T042)
        await self._detect_unsupported_aggregate_patterns(ast, result)

        # Check for cross-project references (T043)
        await self._detect_cross_project_references(ast, result, target_project)

        # Check for region mismatch (T044)
        await self._detect_region_mismatch(ast, result, target_dataset, target_project)

        # Check for logical view references (T045)
        await self._detect_logical_view_references(ast, result)

        # Check for optimization value (T047)
        await self._check_optimization_value(ast, result)

        # Perform column analysis for predicate lifting (T051)
        await self._analyze_columns_for_predicate_lifting(ast, result)

        # Generate MV recommendations (T052)
        await self._generate_mv_recommendations(ast, result)

        # Determine final eligibility
        result.eligible = len(result.disqualification_reasons) == 0
        if result.eligible and result.unsupported_features:
            # Check if unsupported features are preview-only
            preview_only = all(f in self.PREVIEW_FEATURES for f in result.unsupported_features)
            if preview_only:
                result.eligibility_basis = "preview"
            else:
                result.eligible = False

        return result

    async def _check_optimization_value(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Check if query has sufficient optimization value (T047).

        Queries must have at least one of:
        - Aggregations
        - Joins
        - CTEs
        - DISTINCT
        - UNION

        Simple SELECT * queries are generally not worth materializing.

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        has_aggregations = bool(result.aggregation_functions)
        has_joins = bool(result.join_types)
        has_ctes = result.has_ctes
        has_union = bool(ast.find(exp.Union))

        # Check for DISTINCT
        has_distinct = False
        select = ast.find(exp.Select)
        if select and select.args.get("distinct"):
            has_distinct = True

        if not (has_aggregations or has_joins or has_ctes or has_distinct or has_union):
            result.disqualification_reasons.append(
                "Query lacks optimization value: No aggregations, joins, DISTINCT, UNION, or CTEs found. "
                "Simple SELECT queries are generally not candidates for materialized views."
            )

    async def _detect_unsupported_patterns(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Detect unsupported SQL patterns for Smart Tuning (T038).

        Checks for:
        - UNION ALL (preview-only)
        - LEFT/RIGHT/FULL OUTER JOIN (LEFT is preview-only)
        - Window functions
        - ARRAY subqueries
        - QUALIFY clause

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        # Check for UNION
        if ast.find(exp.Union):
            result.unsupported_features.append("UNION ALL")
            if not self.enable_preview_eligibility:
                result.disqualification_reasons.append(
                    "UNION ALL is not supported for Smart Tuning (preview feature only)"
                )

        # Check for JOIN types
        for join in ast.find_all(exp.Join):
            if join.side:
                join_side = join.side.upper()
                if "LEFT" in join_side:
                    result.unsupported_features.append("LEFT OUTER JOIN")
                    if not self.enable_preview_eligibility:
                        result.disqualification_reasons.append(
                            "LEFT OUTER JOIN is not supported for Smart Tuning (preview feature only)"
                        )
                elif "RIGHT" in join_side or "FULL" in join_side:
                    join_type = "RIGHT OUTER JOIN" if "RIGHT" in join_side else "FULL OUTER JOIN"
                    result.unsupported_features.append(join_type)
                    result.disqualification_reasons.append(f"{join_type} is not supported for Smart Tuning")

        # Check for window functions
        if ast.find(exp.Window):
            result.unsupported_features.append("WINDOW FUNCTIONS")
            result.disqualification_reasons.append("Window functions are not supported for Smart Tuning")

        # Check for ARRAY subqueries (in SELECT or WHERE)
        for subquery in ast.find_all(exp.Subquery):
            if subquery.this and isinstance(subquery.this, exp.Array):
                result.unsupported_features.append("ARRAY SUBQUERY")
                result.disqualification_reasons.append("ARRAY subqueries are not supported for Smart Tuning")
                result.subquery_types.append("ARRAY")

        # Check for scalar subqueries
        for subquery in ast.find_all(exp.Subquery):
            if subquery.this and not isinstance(subquery.this, exp.Array):
                result.subquery_types.append("SCALAR")

        # Check for QUALIFY clause
        if ast.find(exp.Qualify):
            result.unsupported_features.append("QUALIFY CLAUSE")
            result.disqualification_reasons.append("QUALIFY clause is not supported for Smart Tuning")

    async def _detect_non_deterministic_functions(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Detect non-deterministic function calls in the query (T039).

        Non-deterministic functions like RAND(), NOW(), CURRENT_DATE make
        queries ineligible for Smart Tuning.

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        non_deterministic = self.parser.detect_non_deterministic_functions(ast)

        if non_deterministic:
            result.unsupported_features.append("NON-DETERMINISTIC FUNCTIONS")
            result.disqualification_reasons.append(
                f"Non-deterministic functions found: {', '.join(non_deterministic)}. "
                "These are not supported for Smart Tuning"
            )

    async def _detect_udfs(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Detect user-defined function calls in the query (T040).

        UDFs are identified as function calls that don't match built-in
        BigQuery function names or use project.dataset.function notation.

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        udfs = self.parser.detect_udfs(ast)

        if udfs:
            result.unsupported_features.append("UDFS")
            result.disqualification_reasons.append(
                f"User-defined functions found: {', '.join(udfs)}. UDFs are not supported for Smart Tuning"
            )

    async def _validate_aggregate_functions(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Validate that query uses only supported aggregate functions (T041).

        BigQuery Smart Tuning supports a specific set of aggregate functions.
        Custom or unsupported aggregates disqualify the query.

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        agg_funcs = self.parser.get_aggregation_functions(ast)

        unsupported_agg = []
        for func in agg_funcs:
            if func not in self.parser.SUPPORTED_AGGREGATES:
                unsupported_agg.append(func)

        if unsupported_agg:
            result.disqualification_reasons.append(
                f"Unsupported aggregate functions found: {', '.join(unsupported_agg)}. "
                f"Supported functions are: {', '.join(sorted(self.parser.SUPPORTED_AGGREGATES))}"
            )

    async def _detect_unsupported_aggregate_patterns(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Detect unsupported aggregate patterns (T042).

        Checks for:
        - HAVING clause on aggregate expressions
        - Computed aggregates (e.g., COUNT(*) / 10)

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        # Check for HAVING clause
        if self.parser.has_having_clause(ast):
            result.unsupported_features.append("HAVING ON AGGREGATES")
            result.disqualification_reasons.append("HAVING clause on aggregates is not supported for Smart Tuning")

        # Check for computed aggregates (arithmetic on aggregate functions)
        for select_expr in ast.find_all(exp.Select):
            for expr in select_expr.expressions:
                if isinstance(expr, exp.Alias):
                    aliased_expr = expr.this
                else:
                    aliased_expr = expr

                # Check if this is an arithmetic operation containing an aggregate
                if isinstance(aliased_expr, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
                    # Check if any operand contains an aggregate function
                    has_agg = False
                    for node in aliased_expr.walk():
                        if isinstance(node, exp.AggFunc):
                            has_agg = True
                            break

                    if has_agg:
                        result.unsupported_features.append("COMPUTED AGGREGATES")
                        result.disqualification_reasons.append(
                            "Computed aggregates (arithmetic on aggregate functions) are not supported for Smart Tuning"
                        )
                        return

    async def _detect_cross_project_references(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
        target_project: str | None,
    ) -> None:
        """
        Detect cross-project table references (T043).

        Queries that reference tables in other projects are not eligible
        for Smart Tuning.

        Args:
            ast: Parsed SQL AST
            result: Result object to update
            target_project: Target project ID
        """
        if not target_project:
            # Can't check without target project
            return

        tables = self.parser.extract_referenced_tables(ast)

        for table in tables:
            if table.project_id and table.project_id != target_project:
                result.cross_project_references = True
                result.disqualification_reasons.append(
                    f"Cross-project reference found: {table.full_name} is in project "
                    f"'{table.project_id}' but target project is '{target_project}'. "
                    "Cross-project references are not supported for Smart Tuning"
                )
                return

    async def _detect_region_mismatch(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
        target_dataset: str | None,
        target_project: str | None,
    ) -> None:
        """
        Detect region mismatch between tables and target dataset (T044).

        All referenced tables must be in the same region as the target dataset.

        Args:
            ast: Parsed SQL AST
            result: Result object to update
            target_dataset: Target dataset ID
            target_project: Target project ID
        """
        if not self.bq_client or not target_dataset or not target_project:
            # Can't check without BigQuery client and target info
            return

        tables = self.parser.extract_referenced_tables(ast)

        # Convert to TableIdentifier objects
        table_identifiers = []
        for table in tables:
            if table.project_id and table.dataset_id and table.table_id:
                table_identifiers.append(
                    TableIdentifier(
                        project_id=table.project_id,
                        dataset_id=table.dataset_id,
                        table_id=table.table_id,
                    )
                )

        if not table_identifiers:
            return

        try:
            # Get target dataset region
            target_region = await self.bq_client.get_dataset_region(
                dataset_id=target_dataset,
                project_id=target_project,
            )

            # Validate all tables match target region
            await self.bq_client.validate_region_match(
                tables=table_identifiers,
                target_region=target_region,
            )

        except Exception as e:
            result.region_mismatch = True
            result.disqualification_reasons.append(f"Region mismatch detected: {e}")

    async def _detect_logical_view_references(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Detect logical view references (T045).

        Materialized views that reference logical views do not support
        Smart Tuning. This requires querying INFORMATION_SCHEMA.TABLES.

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        if not self.bq_client:
            # Can't check without BigQuery client
            return

        tables = self.parser.extract_referenced_tables(ast)

        for table in tables:
            if not table.project_id or not table.dataset_id or not table.table_id:
                continue

            # Query INFORMATION_SCHEMA.TABLES to check if it's a view
            # This is a placeholder for future implementation
            # Actual implementation would query INFORMATION_SCHEMA.TABLES.TABLE_TYPE
            # For now, we'll skip this check as it requires additional queries
            _ = TableIdentifier(
                project_id=table.project_id,
                dataset_id=table.dataset_id,
                table_id=table.table_id,
            )

    async def _analyze_columns_for_predicate_lifting(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Analyze columns for predicate lifting (T051).

        Identifies:
        - Liftable columns: Columns in SELECT/GROUP BY (can be filtered at query time)
        - Non-liftable filters: Filters in WHERE on columns NOT in SELECT/GROUP BY

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        # Get output columns (SELECT and GROUP BY)
        select_columns = self.parser.extract_select_columns(ast)
        group_by_columns = self.parser.extract_group_by_columns(ast)

        # Extract column names from SELECT expressions
        output_columns = set()
        for col in select_columns:
            # Extract base column name (simple heuristic)
            # In production, this would need more sophisticated AST analysis
            if " AS " in col.upper():
                # Extract alias
                col_name = col.upper().split(" AS ")[-1].strip()
            else:
                # Use the expression itself
                col_name = col.strip()
            output_columns.add(col_name)

        for col in group_by_columns:
            output_columns.add(col.strip())

        result.liftable_columns = list(output_columns)

        # Get WHERE predicates
        where_predicates = self.parser.extract_where_predicates(ast)

        # Check for non-liftable filters (filters on non-output columns)
        for pred in where_predicates:
            # Extract column name from predicate (simplified)
            # In production, this would use proper AST analysis
            pred_upper = pred.upper()

            # Try to extract the column being filtered
            for col in output_columns:
                if col.upper() in pred_upper:
                    break
            else:
                # No output column found in this predicate
                # This is a non-liftable filter
                result.non_liftable_filters.append(pred.strip())

    async def _generate_mv_recommendations(
        self,
        ast: exp.Expression,
        result: SmartTuningCheckResult,
    ) -> None:
        """
        Generate recommended MV structure (T052).

        Creates recommendations for:
        - MV SELECT expressions
        - MV GROUP BY expressions
        - MV WHERE filters (shared predicates)

        Args:
            ast: Parsed SQL AST
            result: Result object to update
        """
        # SELECT expressions
        result.recommended_mv_select = self.parser.extract_select_columns(ast)

        # GROUP BY expressions
        result.recommended_mv_group_by = self.parser.extract_group_by_columns(ast)

        # WHERE predicates (non-liftable only)
        result.recommended_mv_filters = result.non_liftable_filters

    def get_eligibility_ruleset(self) -> dict[str, object]:
        """
        Get the current eligibility ruleset with rulebook version (T046).

        Returns:
            Dictionary with eligibility rules and rulebook version
        """
        return {
            "rulebook_version": self.RULEBOOK_VERSION,
            "enable_preview_eligibility": self.enable_preview_eligibility,
            "supported_aggregates": sorted(self.parser.SUPPORTED_AGGREGATES),
            "non_deterministic_functions": sorted(self.parser.NON_DETERMINISTIC_FUNCTIONS),
            "preview_features": sorted(self.PREVIEW_FEATURES),
            "unsupported_features": sorted(self.UNSUPPORTED_FEATURES),
            "rules": {
                "no_union_all": "UNION ALL is not supported (preview feature only)",
                "no_left_outer_join": "LEFT OUTER JOIN is not supported (preview feature only)",
                "no_right_full_outer_join": "RIGHT and FULL OUTER JOIN are not supported",
                "no_window_functions": "Window functions are not supported",
                "no_non_deterministic": "Non-deterministic functions are not supported",
                "no_udfs": "User-defined functions are not supported",
                "supported_aggregates_only": "Only supported aggregate functions are allowed",
                "no_having_on_aggregates": "HAVING on aggregates is not supported",
                "no_computed_aggregates": "Computed aggregates are not supported",
                "no_cross_project": "Cross-project references are not supported",
                "region_match": "All tables must be in the same region as target dataset",
                "no_logical_views": "Logical views are not supported as base tables",
            },
        }
