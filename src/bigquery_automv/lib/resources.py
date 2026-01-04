"""Resource management utilities."""

import pkgutil
from pathlib import Path

RESOURCE_PACKAGE = "bigquery_automv.resources"


def get_sql_resource(resource_name: str) -> str:
    """Load SQL resource content.

    Args:
        resource_name: Name of the SQL file (e.g., 'init_metadata_table.sql')

    Returns:
        Content of the SQL file as a string.

    Raises:
        FileNotFoundError: If resource cannot be found.
    """
    try:
        # Try loading via pkgutil (works for installed packages)
        content = pkgutil.get_data(RESOURCE_PACKAGE + ".sql", resource_name)
        if content:
            return content.decode("utf-8")

        # Fallback for development mode if package structure isn't fully installed
        # Look in source tree
        # This is a bit hacky but helps in dev environment
        # Assuming current file is in src/bigquery_automv/lib/
        base_path = Path(__file__).parent.parent / "resources" / "sql"
        file_path = base_path / resource_name
        if file_path.exists():
            return file_path.read_text(encoding="utf-8")

    except Exception:
        pass

    raise FileNotFoundError(f"Resource {resource_name} not found in {RESOURCE_PACKAGE}.sql")
