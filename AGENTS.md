# Repository instructions

- Use `uv` for Python environment, dependency, and command management.
- Install the project, development tools, and all optional dependencies with `uv sync --all-extras`.
- Run project commands through `uv run`, for example `uv run forest3d --help` or `uv run pytest`.
- Run the complete terrain-backed demo with `uv run --all-extras forest3d demo`.
- Add or update dependencies through `uv add` (and `uv add --dev` for development-only dependencies) rather than editing dependency lists by hand.
