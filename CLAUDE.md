# CLAUDE.md

This project uses **bd (beads)** for issue tracking. See @AGENTS.md for workflow details.

## Project Overview

**bigquery-automv** analyzes BigQuery INFORMATION_SCHEMA.JOBS query history to identify candidates for BigQuery smart tuning, then automatically creates materialized views (MVs) for applicable queries.

Smart tuning allows BigQuery to automatically reroute queries to use materialized views when possible, improving performance without requiring query changes.

**Core functionality:**
- Analyze historical query patterns from INFORMATION_SCHEMA.JOBS
- Identify repetitive queries that would benefit from materialization
- Generate and deploy optimized materialized views
- Track MV usage and effectiveness over time

## Package Management

**CRITICAL**: This project uses `uv` exclusively for Python package management.

- **DO NOT** use `pip install`, `pip freeze`, `virtualenv`, `poetry`, or similar tools
- **DO NOT** manually edit `[project]`, `[project.optional-dependencies]`, `[project.scripts]`, or `[build-system]` in pyproject.toml
- **DO** use `uv` commands: `uv add <package>`, `uv add --dev <package>`, `uv sync`, `uv lock`

**pyproject.toml**: Only manually edit `[tool.*]` sections. Dependencies are managed by `uv add` commands.

## Code Quality

### Pre-commit Hooks

Pre-commit hooks are configured in `.pre-commit-config.yaml`. They run automatically on git commit.

To install hooks (run once per machine):
```bash
pre-commit install
```

To manually run all hooks:
```bash
pre-commit run --all-files
```

### Makefile Commands

Format and lint code using the provided Makefile:

```bash
make style      # Format code with ruff
make lint       # Check code with ruff
make test       # Run tests with pytest
make check      # Run both format and lint checks
```
These use `uvx ruff` to ensure consistent versions without manual installation.

### Testing

This project uses **pytest** for testing. Tests can be run via `uv run pytest` or the Makefile target:

```bash
make test              # Run all tests via Makefile
uv run pytest          # Run all tests directly
uv run pytest tests/   # Run tests in specific directory
uv run pytest -k test_name  # Run tests matching pattern
uv run pytest -v       # Verbose output
uv run pytest --cov   # Run with coverage report
```

## Changelog Management

This project uses **changie** for changelog management. Changie organizes changes as fragment files that are batched into releases.

### Creating Change Entries

When making changes that should be recorded in the changelog:
```bash
changie new
```
This will prompt for:
- **Component**: The area affected (e.g., cli, analyzer, mv)
- **Kind**: Type of change (added, changed, deprecated, removed, fixed, security)
- **Body**: Description of the change

Flags for automation:
- `--kind string` - Set change kind without prompt
- `--component string` - Set component without prompt
- `--body string` - Set body without prompt
- `--editor` - Edit body message using $EDITOR
- `--dry-run` - Print fragment instead of writing

### Batching Releases

When preparing a release, `changie batch` and `uv version` are used together:

```bash
# Always use explicit versions
changie batch 1.2.3
uv version 1.2.3
```

**Version bump options (uv):**
- `--bump major` - Bump major version (0.x.x -> 1.x.x)
- `--bump minor` - Bump minor version (1.0.x -> 1.1.x)
- `--bump patch` - Bump patch version (1.0.0 -> 1.0.1)
- `1.2.3` - Set explicit version

**Important:**
- Versions are forward-only (never downgrade)
- Run `changie batch` first to generate the changelog
- Run `uv version` to update `pyproject.toml`
- Commit both changes together as part of the release

After batching:
```bash
changie merge    # Merge all versions into one changelog
```

## Architecture Decision Records

This project uses **adrgen** to document Architecture Decision Records (ADRs). ADRs capture significant architectural decisions for future contributors and maintainers.

### Creating ADRs

When making significant architectural decisions:
```bash
adrgen create "The decision title"
```
This creates a numbered ADR file in the `docs/adr/` directory with a template for:
- **Status**: Proposed, accepted, rejected, deprecated, or superseded
- **Context**: Background and problem statement
- **Decision**: The chosen approach
- **Consequences**: Impact and trade-offs

Flags for relationship tracing:
- `--supersedes int` - Mark this ADR as replacing a previous decision
- `--amends int` - Mark this ADR as modifying a previous decision
- `--meta key=value` - Add custom metadata

### Managing ADRs

```bash
adrgen list              # List all ADR files
adrgen status <id>       # Update status of an existing ADR
```

## GitHub Integration

This repository is hosted on GitHub. The `gh` CLI tool is used for GitHub operations.

### Issue Tracking

GitHub issues can be filed or discovered and linked to beads for external tracking:

```bash
# View existing issues
gh issue list
gh issue view <number>

# Create a new issue
gh issue create --title "Title" --body "Description"

# Search issues
gh issue list --search "query"
gh search issues --open --repo z3z1ma/bigquery-automv "query"
```

### CI/CD Monitoring

Check CI workflow runs to ensure builds are clean before merging:

```bash
# List recent workflow runs
gh run list

# View details of a specific run
gh run view <run-id>

# Watch a run in real-time
gh run watch

# View failed runs only
gh run list --json databaseId,status,conclusion,name | jq '.[] | select(.conclusion == "failure")'
```

### Pull Requests

```bash
gh pr list              # List pull requests
gh pr view <number>     # View PR details
gh pr checks <number>   # Check CI status for a PR
```

## AI Review

Get a second set of expert eyes on code, specs, plans, ADRs, or tests using `gemini`:

```bash
gemini --yolo -m gemini-3-pro-preview -p 'Review this code for issues and improvements'
gemini --yolo -m gemini-3-pro-preview -p 'Critique this ADR and suggest alternatives'
gemini --yolo -m gemini-3-pro-preview -p 'Check this test plan for edge cases'
```

Use cases:
- **Code review** - Catch bugs, security issues, or anti-patterns
- **Spec/plan review** - Validate approach, identify missing requirements
- **ADR critique** - Challenge assumptions, suggest alternatives
- **Test coverage** - Find gaps in test scenarios
