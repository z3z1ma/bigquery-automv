"""BigQuery region handling utilities."""


class RegionHelper:
    """Helper for normalizing BigQuery regions."""

    _REGION_PROJECT_PREFIX = "region-"

    @staticmethod
    def normalize_region(region: str) -> str:
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
        if region_upper.startswith(RegionHelper._REGION_PROJECT_PREFIX.upper()):
            # "region-us" -> "US", "region-eu" -> "EU"
            suffix = region_upper[len(RegionHelper._REGION_PROJECT_PREFIX) :]
            if suffix in {"US", "EU"}:
                return suffix
            # Keep other regions as-is (e.g., "region-asia-northeast1" -> "ASIA-NORTHEAST1" ??
            # Actually standard BQ regions don't usually have region- prefix in location param)
            # But INFORMATION_SCHEMA needs it.
            # If user passes "region-asia-northeast1", we likely want "ASIA-NORTHEAST1"
            return suffix

        # US and EU multi-regions are used directly
        return region_upper

    @staticmethod
    def get_information_schema_region(region: str) -> str:
        """Return the region name for INFORMATION_SCHEMA queries.

        INFORMATION_SCHEMA.JOBS queries use region-scoped projects like
        `region-us.INFORMATION_SCHEMA.JOBS`.

        Args:
            region: Normalized or raw region string

        Returns:
            Region project name like "region-us" or "region-eu"
        """
        region_upper = region.upper().strip()

        # Remove prefix if present to standardize processing
        if region_upper.startswith(RegionHelper._REGION_PROJECT_PREFIX.upper()):
            region_upper = region_upper[len(RegionHelper._REGION_PROJECT_PREFIX) :]

        # For US/EU multi-regions, add "region-" prefix for INFORMATION_SCHEMA
        if region_upper in {"US", "EU"}:
            return f"{RegionHelper._REGION_PROJECT_PREFIX}{region_upper.lower()}"

        # For other regions, add "region-" prefix
        return f"{RegionHelper._REGION_PROJECT_PREFIX}{region_upper.lower()}"
