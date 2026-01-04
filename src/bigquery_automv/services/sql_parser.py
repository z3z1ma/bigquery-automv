"""
SQL Parser Service using sqlglot for BigQuery dialect.

This module provides SQL parsing and analysis capabilities for BigQuery queries,
focusing on Smart Tuning eligibility detection and query structure extraction.
"""

from dataclasses import dataclass

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError


@dataclass
class TableReference:
    """A BigQuery table referenced in a query."""

    project_id: str
    dataset_id: str
    table_id: str
    region: str | None = None
    processed_bytes: int | None = None

    @property
    def full_name(self) -> str:
        """Full table name in project.dataset.table format."""
        return f"{self.project_id}.{self.dataset_id}.{self.table_id}"

    @property
    def is_cross_project(self, other_project: str) -> bool:
        """True if this table is in a different project than the specified one."""
        return self.project_id != other_project


class SQLParseError(Exception):
    """Raised when SQL parsing fails."""

    def __init__(self, message: str, sql: str | None = None) -> None:
        super().__init__(message)
        self.sql = sql


class SQLParser:
    """
    SQL parser wrapper for BigQuery dialect using sqlglot.

    Provides methods for parsing, analyzing, and extracting information from
    BigQuery SQL queries for Smart Tuning eligibility detection.
    """

    # Supported aggregate functions per BigQuery Smart Tuning rules
    SUPPORTED_AGGREGATES = {
        "ANY_VALUE",
        "APPROX_COUNT_DISTINCT",
        "ARRAY_AGG",
        "AVG",
        "BIT_AND",
        "BIT_OR",
        "BIT_XOR",
        "COUNT",
        "COUNTIF",
        "HLL_COUNT.INIT",
        "LOGICAL_AND",
        "LOGICAL_OR",
        "MAX",
        "MIN",
        "MAX_BY",
        "MIN_BY",
        "SUM",
    }

    # Non-deterministic functions that disqualify Smart Tuning
    NON_DETERMINISTIC_FUNCTIONS = {
        "RAND",
        "CURRENT_DATE",
        "SESSION_USER",
        "CURRENT_TIME",
        "CURRENT_TIMESTAMP",
        "NOW",
        "GENERATE_UUID",
        "UUID_GENERATE",
    }

    def __init__(self, dialect: str = "bigquery") -> None:
        """
        Initialize the SQL parser.

        Args:
            dialect: SQL dialect to use (default: "bigquery")
        """
        self.dialect = dialect

    def parse_query(self, sql: str) -> exp.Expression:
        """
        Parse SQL query into an AST using BigQuery dialect.

        Args:
            sql: SQL query string to parse

        Returns:
            Parsed AST as sqlglot Expression

        Raises:
            SQLParseError: If parsing fails
        """
        if not sql or not sql.strip():
            raise SQLParseError("SQL query is empty", sql)

        try:
            return parse_one(sql, dialect=self.dialect)
        except ParseError as e:
            raise SQLParseError(f"Failed to parse SQL: {e}", sql) from e

    def get_aggregation_functions(self, ast: exp.Expression) -> list[str]:
        """
        Extract aggregate function names from the query.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of aggregate function names (e.g., ['COUNT', 'SUM', 'AVG'])
        """
        agg_funcs: list[str] = []
        for func in ast.find_all(exp.AggFunc):
            # Use class name instead of func.name to handle COUNT(*) correctly
            # sqlglot's Count class has name='*', but we want 'COUNT'
            func_name = type(func).__name__.upper()
            agg_funcs.append(func_name)
        return agg_funcs

    def get_join_types(self, ast: exp.Expression) -> list[str]:
        """
        Extract join types from the query.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of join types (e.g., ['INNER', 'LEFT', 'CROSS'])
        """
        join_types: list[str] = []
        for join in ast.find_all(exp.Join):
            if join.side:
                # Parse join side (e.g., "LEFT", "RIGHT", "FULL")
                join_side = join.side.upper()
                if "OUTER" in join_side:
                    join_side = join_side.replace("OUTER", "").strip()
                join_types.append(join_side)
            else:
                # Default join is INNER
                join_types.append("INNER")
        return join_types

    def has_ctes(self, ast: exp.Expression) -> bool:
        """
        Check if query contains Common Table Expressions (CTEs).

        Args:
            ast: Parsed SQL AST

        Returns:
            True if query has WITH clause with CTEs
        """
        with_clause = ast.find(exp.With)
        return with_clause is not None

    def extract_where_predicates(self, ast: exp.Expression) -> list[str]:
        """
        Extract WHERE clause predicates as SQL strings.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of WHERE clause predicates (top-level AND conditions)
        """
        where_clause = ast.find(exp.Where)
        if not where_clause:
            return []

        predicates: list[str] = []

        # Extract top-level AND conditions
        where_expr = where_clause.this

        # If it's an AND, split into individual predicates
        if isinstance(where_expr, exp.And):
            for predicate in where_expr.flatten():
                if predicate:
                    predicates.append(predicate.sql(dialect=self.dialect))
        elif where_expr:
            # Single predicate
            predicates.append(where_expr.sql(dialect=self.dialect))

        return predicates

    def extract_select_columns(self, ast: exp.Expression) -> list[str]:
        """
        Extract SELECT column expressions as SQL strings.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of SELECT expressions
        """
        select = ast.find(exp.Select)
        if not select:
            return []

        columns: list[str] = []
        for column in select.expressions:
            columns.append(column.sql(dialect=self.dialect))

        return columns

    def extract_group_by_columns(self, ast: exp.Expression) -> list[str]:
        """
        Extract GROUP BY column expressions as SQL strings.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of GROUP BY expressions
        """
        select = ast.find(exp.Select)
        if not select or not select.args.get("group"):
            return []

        group_exprs = select.args["group"]
        columns: list[str] = []

        # group can be a single expression or a tuple
        if isinstance(group_exprs, exp.Tuple):
            for expr in group_exprs.expressions:
                columns.append(expr.sql(dialect=self.dialect))
        elif group_exprs:
            columns.append(group_exprs.sql(dialect=self.dialect))

        return columns

    def detect_unsupported_patterns(self, ast: exp.Expression) -> tuple[bool, list[str]]:
        """
        Detect unsupported SQL patterns for Smart Tuning.

        Args:
            ast: Parsed SQL AST

        Returns:
            Tuple of (has_unsupported, list_of_reasons)
        """
        reasons: list[str] = []

        # Check for UNION ALL
        if ast.find(exp.Union):
            reasons.append("UNION ALL not supported for Smart Tuning")

        # Check for LEFT/RIGHT/FULL OUTER JOIN
        for join in ast.find_all(exp.Join):
            if join.side:
                join_side = join.side.upper()
                if "LEFT" in join_side or "RIGHT" in join_side or "FULL" in join_side:
                    reasons.append(f"{join_side} OUTER JOIN not supported for Smart Tuning")

        # Check for window functions
        if ast.find(exp.Window):
            reasons.append("Window functions not supported for Smart Tuning")

        # Check for QUALIFY clause
        if ast.find(exp.Qualify):
            reasons.append("QUALIFY clause not supported for Smart Tuning")

        return (len(reasons) > 0, reasons)

    def detect_non_deterministic_functions(self, ast: exp.Expression) -> list[str]:
        """
        Detect non-deterministic function calls in the query.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of non-deterministic function names found
        """
        found: list[str] = []

        # Check for function nodes
        for func in ast.find_all(exp.Func):
            if func.name and func.name.upper() in self.NON_DETERMINISTIC_FUNCTIONS:
                func_name = func.name.upper()
                if func_name not in found:
                    found.append(func_name)

        # Check for specific expression types that are non-deterministic
        # sqlglot parses these as special expression types, not Func nodes
        non_deterministic_exprs = {
            exp.CurrentDate: "CURRENT_DATE",
            exp.CurrentTime: "CURRENT_TIME",
            exp.CurrentTimestamp: "CURRENT_TIMESTAMP",
            exp.Rand: "RAND",
            exp.CurrentUser: "SESSION_USER",
            exp.CurrentDatetime: "CURRENT_DATETIME",
        }

        for expr_type, func_name in non_deterministic_exprs.items():
            if ast.find(expr_type):
                if func_name not in found:
                    found.append(func_name)

        return found

    def detect_udfs(self, ast: exp.Expression) -> list[str]:
        """
        Detect user-defined function calls in the query.

        UDFs are identified as function calls that don't match built-in
        BigQuery function names.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of potential UDF names found
        """
        # Built-in BigQuery functions (simplified set)
        builtin_functions = {
            *self.SUPPORTED_AGGREGATES,
            *self.NON_DETERMINISTIC_FUNCTIONS,
            "ABS",
            "ACOS",
            "ARRAY",
            "ASIN",
            "ATAN",
            "BIT_COUNT",
            "CEIL",
            "COALESCE",
            "CONCAT",
            "COS",
            "DATE",
            "DATETIME",
            "DIV",
            "ENDS_WITH",
            "EXP",
            "FLOOR",
            "FROM_BASE64",
            "GREATEST",
            "IFNULL",
            "LEAST",
            "LENGTH",
            "LN",
            "LOG",
            "LOWER",
            "LPAD",
            "LTRIM",
            "MAX",
            "MIN",
            "MOD",
            "NULLIF",
            "POWER",
            "RAND",
            "REGEXP_CONTAINS",
            "REGEXP_EXTRACT",
            "REGEXP_REPLACE",
            "REPLACE",
            "ROUND",
            "RPAD",
            "RTRIM",
            "SIN",
            "SPLIT",
            "SQRT",
            "STARTS_WITH",
            "STRPOS",
            "SUBSTR",
            "TAN",
            "TIMESTAMP",
            "TO_BASE64",
            "TRIM",
            "UPPER",
            # Add more as needed
        }

        # Special tokens that should not be treated as UDFs
        special_tokens = {"*", "("}

        udfs: list[str] = []

        for func in ast.find_all(exp.Func):
            # Check if function name is not a built-in
            if func.name:
                func_name_upper = func.name.upper()
                # Skip special tokens like *
                if func_name_upper in special_tokens:
                    continue
                # Functions with dots are likely UDFs (project.dataset.function)
                if "." in func.name or func_name_upper not in builtin_functions:
                    if func.name not in udfs:
                        udfs.append(func.name)

        return udfs

    def extract_referenced_tables(self, ast: exp.Expression) -> list[TableReference]:
        """
        Extract table references from the query.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of TableReference objects
        """
        tables: list[TableReference] = []
        seen: set[tuple[str, str, str]] = set()

        for table in ast.find_all(exp.Table):
            # Extract table parts using sqlglot's catalog/db/this structure
            # catalog = project, db = dataset, this = table_name
            project_id = table.catalog or ""
            dataset_id = table.db or ""
            table_id = table.name or ""

            # Fallback: if catalog/db are empty, try parsing from the full name
            # This handles cases where sqlglot doesn't properly decompose the name
            if not project_id and not dataset_id:
                table_name = table.name or ""
                parts = table_name.split(".")

                if len(parts) == 3:
                    project_id, dataset_id, table_id = parts
                elif len(parts) == 2:
                    dataset_id, table_id = parts
                else:
                    table_id = parts[0] if parts else ""

            # Deduplicate
            key = (project_id, dataset_id, table_id)
            if key not in seen and table_id:
                seen.add(key)
                tables.append(
                    TableReference(
                        project_id=project_id,
                        dataset_id=dataset_id,
                        table_id=table_id,
                    )
                )

        return tables

    def normalize_query(
        self,
        sql: str,
        replace_literals: bool = True,
        normalize_identifiers: bool = True,
    ) -> str:
        """
        Normalize SQL query for pattern matching.

        Replaces literals with placeholders and normalizes identifiers
        to group queries that differ only in literal values.

        Args:
            sql: SQL query string to normalize
            replace_literals: Replace literal values with placeholders
            normalize_identifiers: Normalize identifier case and quoting

        Returns:
            Normalized SQL string
        """
        ast = self.parse_query(sql)

        if replace_literals:

            def literal_replacer(node: exp.Expression) -> exp.Expression | None:
                """Replace literals with placeholders."""
                if isinstance(node, exp.Literal):
                    # Replace with placeholder
                    if node.is_string:
                        return exp.Literal.string("__LIT__")
                    return exp.Literal.number("__LIT__")
                return None

            ast = ast.transform(literal_replacer)

        if normalize_identifiers:
            # Normalize identifier case (BigQuery is case-insensitive)
            # sqlglot handles this via the dialect
            pass

        return ast.sql(dialect=self.dialect)

    def is_aggregate_query(self, ast: exp.Expression) -> bool:
        """
        Check if query contains aggregations (GROUP BY or aggregate functions).

        Args:
            ast: Parsed SQL AST

        Returns:
            True if query has GROUP BY or aggregate functions
        """
        # Check for GROUP BY
        select = ast.find(exp.Select)
        if select and select.args.get("group"):
            return True

        # Check for aggregate functions
        agg_funcs = self.get_aggregation_functions(ast)
        return len(agg_funcs) > 0

    def has_having_clause(self, ast: exp.Expression) -> bool:
        """
        Check if query has HAVING clause.

        HAVING on aggregates is not supported for Smart Tuning.

        Args:
            ast: Parsed SQL AST

        Returns:
            True if query has HAVING clause
        """
        select = ast.find(exp.Select)
        return select is not None and select.args.get("having") is not None

    def extract_order_by_columns(self, ast: exp.Expression) -> list[str]:
        """
        Extract ORDER BY column expressions as SQL strings.

        Args:
            ast: Parsed SQL AST

        Returns:
            List of ORDER BY expressions
        """
        select = ast.find(exp.Select)
        if not select or not select.args.get("order"):
            return []

        order_exprs = select.args["order"]
        columns: list[str] = []

        for expr in order_exprs.expressions if hasattr(order_exprs, "expressions") else [order_exprs]:
            columns.append(expr.sql(dialect=self.dialect))

        return columns

    def get_query_structure(self, sql: str) -> dict[str, object]:
        """
        Get comprehensive query structure analysis.

        Args:
            sql: SQL query string to analyze

        Returns:
            Dictionary with query structure information
        """
        ast = self.parse_query(sql)

        agg_funcs = self.get_aggregation_functions(ast)
        join_types = self.get_join_types(ast)
        has_unsupported, unsupported_reasons = self.detect_unsupported_patterns(ast)
        non_deterministic = self.detect_non_deterministic_functions(ast)
        udfs = self.detect_udfs(ast)

        return {
            "has_aggregations": self.is_aggregate_query(ast),
            "aggregation_functions": agg_funcs,
            "has_group_by": len(self.extract_group_by_columns(ast)) > 0,
            "group_by_columns": self.extract_group_by_columns(ast),
            "has_join": len(join_types) > 0,
            "join_types": join_types,
            "has_ctes": self.has_ctes(ast),
            "has_where": len(self.extract_where_predicates(ast)) > 0,
            "where_predicates": self.extract_where_predicates(ast),
            "has_order_by": len(self.extract_order_by_columns(ast)) > 0,
            "order_by_columns": self.extract_order_by_columns(ast),
            "has_having": self.has_having_clause(ast),
            "has_unsupported_patterns": has_unsupported,
            "unsupported_reasons": unsupported_reasons,
            "has_non_deterministic_functions": len(non_deterministic) > 0,
            "non_deterministic_functions": non_deterministic,
            "has_udfs": len(udfs) > 0,
            "udfs": udfs,
            "referenced_tables": [
                {"project": t.project_id, "dataset": t.dataset_id, "table": t.table_id}
                for t in self.extract_referenced_tables(ast)
            ],
            "select_columns": self.extract_select_columns(ast),
        }
